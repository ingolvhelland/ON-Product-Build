"""
Brief-decision CLI - the human gate over a written brief (PB-022, PB-023).

Records Ingolv's actual decision - continue, revise, or drop - for each
opportunity's most recent brief. Three-way, not binary approve/reject: a
brief can be good enough to act on, need real rework before it is, or
reveal that the opportunity isn't worth pursuing after all.

Only the most recent brief per opportunity is presented, same reasoning as
onbuild/admission.py: a brief can be rewritten (after a "revise" decision,
or because the underlying evidence or company knowledge changed), and only
the latest is live for a real decision. Earlier briefs are never deleted.

No AI involved, same as onbuild/review.py and onbuild/admission.py.

Run it directly:

    python -m onbuild.brief_decision

Skipped items stay undecided; re-run the tool anytime to continue.
"""

import sqlite3

from onbuild.db.schema import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt() -> str:
    while True:
        choice = (
            input("[c]ontinue / [r]evise / [d]rop / [s]kip / [q]uit > ")
            .strip()
            .lower()
        )
        if choice in ("c", "r", "d", "s", "q"):
            return choice
        print("Please enter c, r, d, s, or q.")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT b.id, b.opportunity_id, o.title, o.organisation, o.track,
               b.company_profile, b.field_positioning, b.position_fit,
               b.candidacy_fit_summary, b.strategic_approach
        FROM briefs b
        JOIN opportunities o ON o.id = b.opportunity_id
        WHERE b.id IN (SELECT MAX(id) FROM briefs GROUP BY opportunity_id)
          AND b.human_decision IS NULL
        ORDER BY b.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending brief decisions.")
            return

        continued = revised = dropped = skipped = 0
        for (
            brief_id,
            opp_id,
            title,
            organisation,
            track,
            company_profile,
            field_positioning,
            position_fit,
            candidacy_fit_summary,
            strategic_approach,
        ) in rows:
            print(
                f"\n=== Opportunity #{opp_id}: {title} — "
                f"{organisation or '(no organisation)'} [{track}] ==="
            )
            print(f"Brief #{brief_id}")
            print(f"\nCompany profile: {company_profile}")
            print(f"\nField positioning: {field_positioning}")
            print(f"\nPosition fit: {position_fit}")
            print(f"\nCandidacy fit: {candidacy_fit_summary}")
            print(f"\nStrategic approach: {strategic_approach}")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "c":
                conn.execute(
                    "UPDATE briefs SET human_decision='continue', "
                    "decided_at=datetime('now') WHERE id=?",
                    (brief_id,),
                )
                conn.commit()
                continued += 1
            elif choice == "r":
                notes = input("Revision notes (what needs to change): ").strip()
                conn.execute(
                    "UPDATE briefs SET human_decision='revise', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (notes or None, brief_id),
                )
                conn.commit()
                revised += 1
            elif choice == "d":
                conn.execute(
                    "UPDATE briefs SET human_decision='drop', "
                    "decided_at=datetime('now') WHERE id=?",
                    (brief_id,),
                )
                conn.commit()
                dropped += 1
            else:
                skipped += 1

        print(
            f"\n=== Summary ===\n"
            f"Continue: {continued}\nRevise: {revised}\n"
            f"Drop: {dropped}\nSkipped: {skipped}"
        )
        print(
            "\nSkipped items remain undecided - re-run this tool anytime "
            "to continue."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
