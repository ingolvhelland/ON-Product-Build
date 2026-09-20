"""
Application-decision CLI - the human gate over a drafted application
(PB-022, PB-025).

Records Ingolv's actual decision - approve, revise, or drop - for each
opportunity's most recent draft. Same three-way shape as the brief gate:
a draft can be good enough to actually submit, need real rework, or reveal
that the opportunity isn't worth pursuing after all.

Only the most recent application per opportunity is presented, same
reasoning as onbuild/admission.py and onbuild/brief_decision.py: a draft
can be rewritten (after "revise", or because the brief or evidence changed
underneath it), and only the latest is live for a real decision. Earlier
drafts are never deleted.

No AI involved, same as every other decision gate in this system.

Run it directly:

    python -m onbuild.application_decision

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
            input("[a]pprove / [r]evise / [d]rop / [s]kip / [q]uit > ")
            .strip()
            .lower()
        )
        if choice in ("a", "r", "d", "s", "q"):
            return choice
        print("Please enter a, r, d, s, or q.")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT a.id, a.opportunity_id, o.title, o.organisation, o.track,
               a.application_format_assessment, a.tailored_cv,
               a.cover_letter_or_message, a.application_form_data,
               a.question_responses, a.portfolio_recommendation
        FROM applications a
        JOIN opportunities o ON o.id = a.opportunity_id
        WHERE a.id IN (SELECT MAX(id) FROM applications GROUP BY opportunity_id)
          AND a.human_decision IS NULL
        ORDER BY a.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending application decisions.")
            return

        approved = revised = dropped = skipped = 0
        for (
            app_id,
            opp_id,
            title,
            organisation,
            track,
            application_format_assessment,
            tailored_cv,
            cover_letter_or_message,
            application_form_data,
            question_responses,
            portfolio_recommendation,
        ) in rows:
            print(
                f"\n=== Opportunity #{opp_id}: {title} — "
                f"{organisation or '(no organisation)'} [{track}] ==="
            )
            print(f"Application #{app_id}")
            print(f"\nFormat assessment: {application_format_assessment}")
            print(f"\nTailored CV:\n{tailored_cv}")
            print(f"\nCover letter / message:\n{cover_letter_or_message}")
            print(f"\nApplication form data:\n{application_form_data}")
            if question_responses:
                print(f"\nQuestion responses:\n{question_responses}")
            print(f"\nPortfolio recommendation: {portfolio_recommendation}")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "a":
                conn.execute(
                    "UPDATE applications SET human_decision='approve', "
                    "decided_at=datetime('now') WHERE id=?",
                    (app_id,),
                )
                conn.commit()
                approved += 1
            elif choice == "r":
                notes = input("Revision notes (what needs to change): ").strip()
                conn.execute(
                    "UPDATE applications SET human_decision='revise', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (notes or None, app_id),
                )
                conn.commit()
                revised += 1
            elif choice == "d":
                conn.execute(
                    "UPDATE applications SET human_decision='drop', "
                    "decided_at=datetime('now') WHERE id=?",
                    (app_id,),
                )
                conn.commit()
                dropped += 1
            else:
                skipped += 1

        print(
            f"\n=== Summary ===\n"
            f"Approve: {approved}\nRevise: {revised}\n"
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
