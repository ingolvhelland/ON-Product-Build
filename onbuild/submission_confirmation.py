"""
Submission-confirmation CLI - the manual gate named in PRODUCT_BUILD_ANCHOR.md's
"Application pipeline, detailed" section, built in PB-029.

Unlike every other gate in this system, this one is not a content
judgment - it doesn't ask whether a draft is good, it asks a plain fact:
did you actually send this? Approving a draft (`onbuild.application_decision`)
means it's good enough to submit; it does not mean it has been. This is
the trigger that moves an opportunity from "drafted" into "submitted,
pending outcome" (PB-022) - `applications.submitted_at` is set, and
`opportunities.lifecycle_status` becomes 'submitted_pending_outcome',
which the outcome agent requires before it will run for that opportunity.

Only approved applications not yet marked submitted are shown. No AI
involved, same as every other gate.

Run it directly:

    python -m onbuild.submission_confirmation

Skipped items stay unconfirmed; re-run the tool anytime to continue.
"""

import sqlite3

from onbuild.db.schema import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt() -> str:
    while True:
        choice = input("Actually submitted? [y]es / [s]kip / [q]uit > ").strip().lower()
        if choice in ("y", "s", "q"):
            return choice
        print("Please enter y, s, or q.")


def pending_confirmations(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT a.id, a.opportunity_id, o.title, o.organisation, o.track
        FROM applications a
        JOIN opportunities o ON o.id = a.opportunity_id
        WHERE a.id IN (SELECT MAX(id) FROM applications GROUP BY opportunity_id)
          AND a.human_decision = 'approve'
          AND a.submitted_at IS NULL
        ORDER BY a.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_confirmations(conn)
        if not rows:
            print("No approved applications awaiting submission confirmation.")
            return

        confirmed = skipped = 0
        for application_id, opportunity_id, title, organisation, track in rows:
            print(
                f"\n=== Opportunity #{opportunity_id}: {title} — "
                f"{organisation or '(no organisation)'} [{track}] ==="
            )
            print(f"Application #{application_id}, approved, not yet marked submitted.")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "y":
                conn.execute(
                    "UPDATE applications SET submitted_at=datetime('now') WHERE id=?",
                    (application_id,),
                )
                conn.execute(
                    "UPDATE opportunities SET lifecycle_status='submitted_pending_outcome' "
                    "WHERE id=?",
                    (opportunity_id,),
                )
                conn.commit()
                confirmed += 1
            else:
                skipped += 1

        print(f"\n=== Summary ===\nConfirmed: {confirmed}\nSkipped: {skipped}")
        print(
            "\nSkipped items remain unconfirmed - re-run this tool anytime "
            "to continue."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
