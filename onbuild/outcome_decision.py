"""
Outcome-decision CLI - the human gate over the outcome agent's message
classification (PB-022, PB-027, PB-029).

Records Ingolv's actual read of each classified message - confirm (the
agent got it right), recategorize (it didn't - pick the correct category),
or ignore (the message wasn't actually decision-relevant - spam, a
duplicate, unrelated). This is the only place opportunities.lifecycle_status
actually changes as a result of a message (PB-022/PB-027: the outcome
agent itself "never mutates pipeline state directly").

Only the most recent undecided outcome per application is presented. No AI
involved, same as every other gate in this system.

PB-026's byproduct-evidence promotion only fires on 'confirm' - a
'recategorize' means Ingolv found something wrong with this agent's read
of the message, so anything it surfaced alongside that read stays on the
ordinary onbuild.review gate rather than inheriting trust; 'ignore' grants
nothing for the same reason.

Run it directly:

    python -m onbuild.outcome_decision

Skipped items stay undecided; re-run the tool anytime to continue.
"""

import sqlite3

from onbuild.db import evidence_ops
from onbuild.db.schema import DB_PATH

_CATEGORIES = (
    "receipt_confirmation",
    "interview",
    "rejection",
    "further_info",
    "other_request",
    "unclear",
)

# What a confirmed/recategorized message's category does to the
# opportunity's lifecycle_status. 'receipt_confirmation' and 'unclear'
# leave it unchanged - a plain acknowledgement or an honestly-uncertain
# read isn't itself grounds to move the opportunity anywhere.
_LIFECYCLE_STATUS_BY_CATEGORY = {
    "interview": "interview",
    "rejection": "rejected",
    "further_info": "awaiting_action",
    "other_request": "awaiting_action",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt() -> str:
    while True:
        choice = (
            input("[c]onfirm / [r]ecategorize / [i]gnore / [s]kip / [q]uit > ")
            .strip()
            .lower()
        )
        if choice in ("c", "r", "i", "s", "q"):
            return choice
        print("Please enter c, r, i, s, or q.")


def _prompt_category() -> str:
    options = ", ".join(_CATEGORIES)
    while True:
        value = input(f"Correct category ({options}) > ").strip().lower()
        if value in _CATEGORIES:
            return value
        print(f"Please enter one of: {options}")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT out.id, out.opportunity_id, o.title, o.organisation,
               out.application_id, out.submission_confirmed, out.category,
               out.rationale, out.suggested_next_step, out.captured_message_text
        FROM outcomes out
        JOIN opportunities o ON o.id = out.opportunity_id
        WHERE out.id IN (SELECT MAX(id) FROM outcomes GROUP BY application_id)
          AND out.human_decision IS NULL
        ORDER BY out.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending outcome decisions.")
            return

        confirmed = recategorized = ignored = skipped = 0
        for (
            outcome_id,
            opportunity_id,
            title,
            organisation,
            application_id,
            submission_confirmed,
            category,
            rationale,
            suggested_next_step,
            captured_message_text,
        ) in rows:
            print(f"\n=== Opportunity #{opportunity_id}: {title} — {organisation or '(no organisation)'} ===")
            print(f"Outcome #{outcome_id} for application #{application_id}")
            print(f"\nCaptured message:\n{captured_message_text}")
            print(f"\nsubmission_confirmed: {bool(submission_confirmed)}")
            print(f"category: {category}")
            print(f"\nRationale: {rationale}")
            if suggested_next_step:
                print(f"\nSuggested next step: {suggested_next_step}")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "c":
                conn.execute(
                    "UPDATE outcomes SET human_decision='confirm', "
                    "decided_category=?, decided_at=datetime('now') WHERE id=?",
                    (category, outcome_id),
                )
                new_status = _LIFECYCLE_STATUS_BY_CATEGORY.get(category)
                if new_status:
                    conn.execute(
                        "UPDATE opportunities SET lifecycle_status=? WHERE id=?",
                        (new_status, opportunity_id),
                    )
                conn.commit()
                confirmed += 1
                promoted_nodes, promoted_edges = (
                    evidence_ops.promote_provisional_evidence(
                        "outcome", opportunity_id
                    )
                )
                if promoted_nodes or promoted_edges:
                    print(
                        f"  -> promoted {len(promoted_nodes)} evidence node(s) "
                        f"and {len(promoted_edges)} edge(s) to 'provisional' "
                        f"(live now; onbuild.digest is the override window)."
                    )
            elif choice == "r":
                decided_category = _prompt_category()
                notes = input("Notes (what the agent got wrong): ").strip()
                conn.execute(
                    "UPDATE outcomes SET human_decision='recategorize', "
                    "decided_category=?, revision_notes=?, "
                    "decided_at=datetime('now') WHERE id=?",
                    (decided_category, notes or None, outcome_id),
                )
                new_status = _LIFECYCLE_STATUS_BY_CATEGORY.get(decided_category)
                if new_status:
                    conn.execute(
                        "UPDATE opportunities SET lifecycle_status=? WHERE id=?",
                        (new_status, opportunity_id),
                    )
                conn.commit()
                recategorized += 1
            elif choice == "i":
                conn.execute(
                    "UPDATE outcomes SET human_decision='ignore', "
                    "decided_at=datetime('now') WHERE id=?",
                    (outcome_id,),
                )
                conn.commit()
                ignored += 1
            else:
                skipped += 1

        print(
            f"\n=== Summary ===\n"
            f"Confirmed: {confirmed}\nRecategorized: {recategorized}\n"
            f"Ignored: {ignored}\nSkipped: {skipped}"
        )
        print(
            "\nSkipped items remain undecided - re-run this tool anytime "
            "to continue."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
