"""
Review CLI: the human gate over everything the curator agent proposes.

Nothing the curator writes (evidence_nodes, evidence_edges, identity_core)
is canonical - every row starts life as status='proposed'. This tool is the
only place that moves a row to 'approved' or 'rejected'. There is no AI
involved here on purpose: the whole point of PB-010 ("monitor and judge, not
parse and populate") is that judgment stays human. Nothing is ever deleted -
a rejection is just a status, so it's always visible and reversible by
re-editing the row.

Run it directly:

    python -m onbuild.review

Skipped items stay status='proposed'; re-run the tool anytime to pick up
where you left off.

Byproduct evidence an evaluation/brief/drafting agent surfaces (as opposed
to the curator's primary intake) never reaches 'proposed' review by this
route once its parent artifact is accepted - it goes straight to
status='provisional' instead, live immediately, with its own override
window at `onbuild.digest` (PB-026). It only shows up here if its parent
artifact was rejected/revised/dropped, or hasn't been decided yet.
"""

import json
import sqlite3

from onbuild.db.schema import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _format_attributes(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (TypeError, ValueError):
        return raw


def _prompt() -> str:
    while True:
        choice = input("[a]pprove / [r]eject / [s]kip / [q]uit > ").strip().lower()
        if choice in ("a", "r", "s", "q"):
            return choice
        print("Please enter a, r, s, or q.")


def review_nodes(conn: sqlite3.Connection) -> tuple[int, int, int, bool]:
    approved = rejected = skipped = 0
    rows = conn.execute(
        "SELECT id, node_type, quality_tag, description, attributes, source "
        "FROM evidence_nodes WHERE status = 'proposed' ORDER BY id"
    ).fetchall()
    if not rows:
        print("No proposed evidence nodes.\n")
        return approved, rejected, skipped, False

    for node_id, node_type, quality_tag, description, attributes, source in rows:
        print(f"\n--- Evidence Node #{node_id} [proposed] ---")
        print(f"Type: {node_type} | Quality: {quality_tag}")
        print(description)
        attrs = _format_attributes(attributes)
        if attrs:
            print(f"Attributes: {attrs}")
        print(f"Source: {source}")

        choice = _prompt()
        if choice == "q":
            return approved, rejected, skipped, True
        if choice == "a":
            conn.execute(
                "UPDATE evidence_nodes SET status='approved', "
                "updated_at=datetime('now') WHERE id=?",
                (node_id,),
            )
            conn.commit()
            approved += 1
        elif choice == "r":
            conn.execute(
                "UPDATE evidence_nodes SET status='rejected', "
                "updated_at=datetime('now') WHERE id=?",
                (node_id,),
            )
            conn.commit()
            rejected += 1
        else:
            skipped += 1

    return approved, rejected, skipped, False


def review_edges(conn: sqlite3.Connection) -> tuple[int, int, int, bool]:
    approved = rejected = skipped = 0
    rows = conn.execute(
        "SELECT id, source_node_id, target_node_id, edge_type, quality_tag, source "
        "FROM evidence_edges WHERE status = 'proposed' ORDER BY id"
    ).fetchall()
    if not rows:
        print("No proposed evidence edges.\n")
        return approved, rejected, skipped, False

    for edge_id, source_id, target_id, edge_type, quality_tag, source in rows:
        src = conn.execute(
            "SELECT status, node_type, quality_tag, description "
            "FROM evidence_nodes WHERE id=?",
            (source_id,),
        ).fetchone()
        tgt = conn.execute(
            "SELECT status, node_type, quality_tag, description "
            "FROM evidence_nodes WHERE id=?",
            (target_id,),
        ).fetchone()

        print(f"\n--- Evidence Edge #{edge_id} [proposed] ---")
        print(f"  #{source_id} [{src[0]}, {src[1]}/{src[2]}]: {src[3]}")
        print(f"     --[{edge_type} / {quality_tag}]-->")
        print(f"  #{target_id} [{tgt[0]}, {tgt[1]}/{tgt[2]}]: {tgt[3]}")
        if src[0] == "rejected" or tgt[0] == "rejected":
            print("  NOTE: one endpoint is REJECTED - this edge is likely stale.")
        print(f"Source: {source}")

        choice = _prompt()
        if choice == "q":
            return approved, rejected, skipped, True
        if choice == "a":
            conn.execute(
                "UPDATE evidence_edges SET status='approved', "
                "updated_at=datetime('now') WHERE id=?",
                (edge_id,),
            )
            conn.commit()
            approved += 1
        elif choice == "r":
            conn.execute(
                "UPDATE evidence_edges SET status='rejected', "
                "updated_at=datetime('now') WHERE id=?",
                (edge_id,),
            )
            conn.commit()
            rejected += 1
        else:
            skipped += 1

    return approved, rejected, skipped, False


def review_identity_facts(conn: sqlite3.Connection) -> tuple[int, int, int, bool]:
    approved = rejected = skipped = 0
    rows = conn.execute(
        "SELECT id, key, value, source FROM identity_core "
        "WHERE status = 'proposed' ORDER BY id"
    ).fetchall()
    if not rows:
        print("No proposed identity facts.\n")
        return approved, rejected, skipped, False

    for fact_id, key, value, source in rows:
        print(f"\n--- Identity Fact #{fact_id}: {key} [proposed] ---")
        print(f"Value: {value}")
        print(f"Source: {source}")

        choice = _prompt()
        if choice == "q":
            return approved, rejected, skipped, True
        if choice == "a":
            # Approving a fact makes it current; any previously-current fact
            # for the same key steps aside (PB-004: revise by inserting a new
            # row, never overwrite - so the old row is kept, just not current).
            conn.execute(
                "UPDATE identity_core SET status='approved', is_current=1 "
                "WHERE id=?",
                (fact_id,),
            )
            conn.execute(
                "UPDATE identity_core SET is_current=0 "
                "WHERE key=? AND id != ? AND is_current=1",
                (key, fact_id),
            )
            conn.commit()
            approved += 1
        elif choice == "r":
            conn.execute(
                "UPDATE identity_core SET status='rejected' WHERE id=?",
                (fact_id,),
            )
            conn.commit()
            rejected += 1
        else:
            skipped += 1

    return approved, rejected, skipped, False


def main() -> None:
    conn = _connect()
    try:
        print("=== Reviewing evidence nodes ===")
        n_a, n_r, n_s, quit_early = review_nodes(conn)

        e_a = e_r = e_s = 0
        if not quit_early:
            print("\n=== Reviewing evidence edges ===")
            e_a, e_r, e_s, quit_early = review_edges(conn)

        i_a = i_r = i_s = 0
        if not quit_early:
            print("\n=== Reviewing identity facts ===")
            i_a, i_r, i_s, quit_early = review_identity_facts(conn)
    finally:
        conn.close()

    print("\n=== Summary ===")
    print(f"Nodes:    {n_a} approved, {n_r} rejected, {n_s} skipped")
    print(f"Edges:    {e_a} approved, {e_r} rejected, {e_s} skipped")
    print(f"Identity: {i_a} approved, {i_r} rejected, {i_s} skipped")
    print(
        "\nSkipped items remain status='proposed' - re-run this tool anytime "
        "to continue."
    )


if __name__ == "__main__":
    main()
