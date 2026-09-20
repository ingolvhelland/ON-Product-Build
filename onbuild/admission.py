"""
Admission-decision CLI - the human gate PB-002 was designed around.

Records Ingolv's actual decision (admit/reject) for each opportunity's most
recent evaluation, alongside the evaluation agent's own suggested_action.
The gap between the two, accumulated across many opportunities, is the
agreement-rate calibration data PB-002 named as the reason admission stays
manual for now - "every evaluation agent call records its suggested action
alongside Ingolv's actual decision from day one, so agreement-rate becomes
real evidence for revisiting a score-band auto-admission threshold later."

Only the most recent evaluation per opportunity is presented for decision -
an opportunity can be evaluated more than once (a fixed agent, a corrected
graph, a future re-evaluation trigger per PB-019's deferred note), and only
the latest one is live for the purpose of a real decision. Earlier
evaluations are never deleted or hidden; they stay queryable for history.

No AI involved, same as onbuild/review.py: this is the one place admission
judgment actually happens.

Run it directly:

    python -m onbuild.admission

Skipped items stay undecided (human_decision remains NULL); re-run the tool
anytime to continue.
"""

import sqlite3

from onbuild.db import evidence_ops
from onbuild.db.schema import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt() -> str:
    while True:
        choice = input("[a]dmit / [r]eject / [s]kip / [q]uit > ").strip().lower()
        if choice in ("a", "r", "s", "q"):
            return choice
        print("Please enter a, r, s, or q.")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT e.id, e.opportunity_id, o.title, o.organisation, o.track,
               e.fit_summary, e.distinctiveness, e.gates_summary,
               e.countercase, e.gaps_summary, e.fit_score, e.fit_tier,
               e.suggested_action
        FROM evaluations e
        JOIN opportunities o ON o.id = e.opportunity_id
        WHERE e.id IN (SELECT MAX(id) FROM evaluations GROUP BY opportunity_id)
          AND e.human_decision IS NULL
        ORDER BY e.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending admission decisions.")
            return

        admitted = rejected = skipped = 0
        for (
            eval_id,
            opp_id,
            title,
            organisation,
            track,
            fit_summary,
            distinctiveness,
            gates_summary,
            countercase,
            gaps_summary,
            fit_score,
            fit_tier,
            suggested_action,
        ) in rows:
            print(
                f"\n=== Opportunity #{opp_id}: {title} — "
                f"{organisation or '(no organisation)'} [{track}] ==="
            )
            print(
                f"Evaluation #{eval_id} | fit_score={fit_score} | "
                f"fit_tier={fit_tier} | suggested_action={suggested_action}"
            )
            print(f"\nFit: {fit_summary}")
            print(f"\nDistinctiveness: {distinctiveness}")
            print(f"\nGates: {gates_summary}")
            print(f"\nCountercase: {countercase}")
            if gaps_summary:
                print(f"\nGaps: {gaps_summary}")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "a":
                conn.execute(
                    "UPDATE evaluations SET human_decision='admit', "
                    "decided_at=datetime('now') WHERE id=?",
                    (eval_id,),
                )
                conn.commit()
                admitted += 1
                # PB-026: admitting this evaluation is the accepting decision
                # that lets any byproduct evidence it surfaced inherit trust
                # and go live as 'provisional', pending onbuild.digest.
                promoted_nodes, promoted_edges = (
                    evidence_ops.promote_provisional_evidence(
                        "evaluation", opp_id
                    )
                )
                if promoted_nodes or promoted_edges:
                    print(
                        f"  -> promoted {len(promoted_nodes)} evidence node(s) "
                        f"and {len(promoted_edges)} edge(s) to 'provisional' "
                        f"(live now; onbuild.digest is the override window)."
                    )
            elif choice == "r":
                conn.execute(
                    "UPDATE evaluations SET human_decision='reject', "
                    "decided_at=datetime('now') WHERE id=?",
                    (eval_id,),
                )
                conn.commit()
                rejected += 1
            else:
                skipped += 1

        print(
            f"\n=== Summary ===\n"
            f"Admitted: {admitted}\nRejected: {rejected}\nSkipped: {skipped}"
        )
        print(
            "\nSkipped items remain undecided - re-run this tool anytime "
            "to continue."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
