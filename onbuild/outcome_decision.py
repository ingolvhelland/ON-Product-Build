"""
Outcome-decision CLI (PB-022, PB-027, PB-029; redesigned PB-040).

The outcome agent now applies most classifications directly (PB-038's
authorship principle: past submission, the recipient authors the fact a
message represents, not Ingolv) - this tool no longer approves every
message before anything happens. It has two separate jobs instead:

1. Resolve (the default, no-argument flow): decide the outcomes the
   agent genuinely could NOT apply on its own - 'unclear' classifications,
   where no real fact yet exists to transcribe, plus any outcome recorded
   before PB-040 existed (this system's own history has exactly two: the
   Accura and Fælles Digital receipt confirmations). Presented the same
   way admission/brief_decision/application_decision are: read what's
   there, pick a category (accepting the agent's own read is one keypress
   when it already gave one), or ignore it as not decision-relevant.

2. Override (`--override <opportunity_id>`): correct an ALREADY-applied
   classification - the exception path, not the norm, for when the agent's
   read turns out to have been wrong. Requires knowing which opportunity;
   looks up its most recent outcome directly.

Byproduct-evidence promotion (PB-026) fires when a human accepts the
agent's own category while resolving ('confirm') - the same inherited-
trust reasoning as every other accepting decision in this system. It
never fires on 'recategorize' or 'override': a demonstrably wrong read
means whatever it surfaced alongside that read stays on the ordinary
onbuild.review gate rather than inheriting trust. It never fires on
'ignore' for the same reason evidence promotion never fires on rejection
elsewhere - there's no accepted parent to inherit trust from.

Run it directly:

    python -m onbuild.outcome_decision
    python -m onbuild.outcome_decision --override <opportunity_id>

Skipped items stay unresolved; re-run the tool anytime to continue.
"""

import argparse
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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt_resolve(category: str) -> str:
    if category == "unclear":
        prompt = "[c]ategorize / [i]gnore / [s]kip / [q]uit > "
        valid = ("c", "i", "s", "q")
    else:
        prompt = f"[a]ccept '{category}' / [c]hange category / [i]gnore / [s]kip / [q]uit > "
        valid = ("a", "c", "i", "s", "q")
    while True:
        choice = input(prompt).strip().lower()
        if choice in valid:
            return choice
        print(f"Please enter one of: {', '.join(valid)}")


def _prompt_category(exclude_unclear: bool = True) -> str:
    options = tuple(c for c in _CATEGORIES if not (exclude_unclear and c == "unclear"))
    shown = ", ".join(options)
    while True:
        value = input(f"Correct category ({shown}) > ").strip().lower()
        if value in options:
            return value
        print(f"Please enter one of: {shown}")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    """Outcomes with nothing applied yet - 'unclear' classifications and
    any pre-PB-040 legacy row. Only the most recent outcome per
    application, same reasoning as before PB-040: an application can
    accumulate more than one message, and only the latest is live for a
    real decision."""
    return conn.execute(
        """
        SELECT out.id, out.opportunity_id, o.title, o.organisation,
               out.application_id, out.submission_confirmed, out.category,
               out.rationale, out.suggested_next_step, out.captured_message_text
        FROM outcomes out
        JOIN opportunities o ON o.id = out.opportunity_id
        WHERE out.id IN (SELECT MAX(id) FROM outcomes GROUP BY application_id)
          AND out.applied_at IS NULL
        ORDER BY out.id
        """
    ).fetchall()


def resolve() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No outcomes awaiting resolution.")
            return

        applied = ignored = skipped = 0
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
            print(f"category (as classified): {category}")
            print(f"\nRationale: {rationale}")
            if suggested_next_step:
                print(f"\nSuggested next step: {suggested_next_step}")

            choice = _prompt_resolve(category)
            if choice == "q":
                break
            if choice == "s":
                skipped += 1
                continue
            if choice == "i":
                conn.execute(
                    "UPDATE outcomes SET human_decision='ignore', "
                    "decided_at=datetime('now') WHERE id=?",
                    (outcome_id,),
                )
                conn.commit()
                ignored += 1
                continue

            if choice == "a":
                final_category = category
                human_decision = "confirm"
            else:  # 'c'
                final_category = _prompt_category()
                human_decision = "recategorize"

            new_status = evidence_ops.apply_outcome(outcome_id, opportunity_id, final_category)
            conn.execute(
                "UPDATE outcomes SET human_decision=?, decided_category=?, "
                "decided_at=datetime('now') WHERE id=?",
                (human_decision, final_category, outcome_id),
            )
            conn.commit()
            applied += 1
            if new_status:
                print(f"  -> lifecycle_status set to '{new_status}'.")
            else:
                print("  -> no lifecycle_status change needed for this category.")

            if human_decision == "confirm":
                promoted_nodes, promoted_edges = evidence_ops.promote_provisional_evidence(
                    "outcome", opportunity_id
                )
                if promoted_nodes or promoted_edges:
                    print(
                        f"  -> promoted {len(promoted_nodes)} evidence node(s) "
                        f"and {len(promoted_edges)} edge(s) to 'provisional' "
                        f"(live now; onbuild.digest is the override window)."
                    )

        print(
            f"\n=== Summary ===\n"
            f"Applied: {applied}\nIgnored: {ignored}\nSkipped: {skipped}"
        )
        print("\nSkipped items remain unresolved - re-run this tool anytime to continue.")
    finally:
        conn.close()


def override(opportunity_id: int) -> None:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT out.id, out.application_id, out.category, out.applied_category,
                   out.applied_at, out.captured_message_text, out.rationale, o.title
            FROM outcomes out
            JOIN opportunities o ON o.id = out.opportunity_id
            WHERE out.opportunity_id = ?
            ORDER BY out.id DESC LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()
        if row is None:
            print(f"No outcome recorded for opportunity #{opportunity_id}.")
            return

        (
            outcome_id, application_id, category, applied_category,
            applied_at, captured_message_text, rationale, title,
        ) = row
        if applied_at is None:
            print(
                f"Outcome #{outcome_id} (opportunity #{opportunity_id}, {title}) has "
                f"nothing applied yet - use the default resolve flow "
                f"(python -m onbuild.outcome_decision, no arguments), not --override."
            )
            return

        print(f"\n=== Opportunity #{opportunity_id}: {title} ===")
        print(f"Outcome #{outcome_id} for application #{application_id}")
        print(f"Currently applied as: {applied_category} (agent originally classified: {category})")
        print(f"\nCaptured message:\n{captured_message_text}")
        print(f"\nOriginal rationale: {rationale}")

        new_category = _prompt_category()
        notes = input("Notes (why this is being overridden): ").strip()

        new_status = evidence_ops.apply_outcome(outcome_id, opportunity_id, new_category)
        conn.execute(
            "UPDATE outcomes SET human_decision='override', decided_category=?, "
            "revision_notes=?, decided_at=datetime('now') WHERE id=?",
            (new_category, notes or None, outcome_id),
        )
        conn.commit()
        if new_status:
            print(f"Overridden to '{new_category}' - lifecycle_status now '{new_status}'.")
        else:
            print(f"Overridden to '{new_category}' - no lifecycle_status change for this category.")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve outcomes awaiting a decision, or override an already-applied one."
    )
    parser.add_argument(
        "--override",
        type=int,
        metavar="OPPORTUNITY_ID",
        help="Correct the most recent already-applied outcome for this opportunity.",
    )
    args = parser.parse_args()
    if args.override is not None:
        override(args.override)
    else:
        resolve()


if __name__ == "__main__":
    main()
