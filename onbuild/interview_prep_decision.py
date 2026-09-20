"""
Interview-prep-decision CLI - the human gate over the interview-preparation
agent's output (PB-022, PB-027, PB-035, PB-036).

Same four-way shape as the brief and application gates: approve, revise,
pause, or drop. 'pause' means "I haven't decided yet, on purpose" - a
paused item keeps reappearing here on every future run, sorted by the
opportunity's own application_deadline (soonest first), the same
reasoning `onbuild.overview` sorts by deadline urgency (PB-032).

Only the most recent interview prep per opportunity is presented. No AI
involved, same as every other gate in this system.

PB-026's byproduct-evidence promotion only fires on 'approve' - the same
reasoning as every other gate: a 'revise' means Ingolv found something
wrong with this agent's research or strategy, so anything it surfaced
alongside that work stays on the ordinary onbuild.review gate rather than
inheriting trust; 'pause' and 'drop' grant nothing for the same reason.

Run it directly:

    python -m onbuild.interview_prep_decision

Skipped items stay undecided; re-run the tool anytime to continue.
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
        choice = (
            input("[a]pprove / [r]evise / [p]ause / [d]rop / [s]kip / [q]uit > ")
            .strip()
            .lower()
        )
        if choice in ("a", "r", "p", "d", "s", "q"):
            return choice
        print("Please enter a, r, p, d, s, or q.")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT ip.id, ip.opportunity_id, o.title, o.organisation,
               o.application_deadline, ip.interviewer_research,
               ip.office_leadership_research, ip.talking_points,
               ip.requirement_coverage, ip.human_decision
        FROM interview_preps ip
        JOIN opportunities o ON o.id = ip.opportunity_id
        WHERE ip.id IN (SELECT MAX(id) FROM interview_preps GROUP BY opportunity_id)
          AND (ip.human_decision IS NULL OR ip.human_decision = 'pause')
        ORDER BY
            CASE WHEN o.application_deadline IS NULL THEN 1 ELSE 0 END,
            o.application_deadline ASC,
            ip.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending interview-prep decisions.")
            return

        approved = revised = paused = dropped = skipped = 0
        for (
            prep_id,
            opp_id,
            title,
            organisation,
            application_deadline,
            interviewer_research,
            office_leadership_research,
            talking_points,
            requirement_coverage,
            prior_decision,
        ) in rows:
            paused_tag = " [PAUSED - revisit]" if prior_decision == "pause" else ""
            deadline_tag = f" | deadline: {application_deadline}" if application_deadline else ""
            print(
                f"\n=== Opportunity #{opp_id}: {title} — "
                f"{organisation or '(no organisation)'}{deadline_tag}{paused_tag} ==="
            )
            print(f"Interview prep #{prep_id}")
            print(f"\nInterviewer research:\n{interviewer_research}")
            print(f"\nOffice leadership research:\n{office_leadership_research}")
            print(f"\nTalking points:\n{talking_points}")
            print(f"\nRequirement coverage:\n{requirement_coverage}")

            choice = _prompt()
            if choice == "q":
                break
            if choice == "a":
                conn.execute(
                    "UPDATE interview_preps SET human_decision='approve', "
                    "decided_at=datetime('now') WHERE id=?",
                    (prep_id,),
                )
                conn.commit()
                approved += 1
                promoted_nodes, promoted_edges = (
                    evidence_ops.promote_provisional_evidence(
                        "interview_prep", opp_id
                    )
                )
                if promoted_nodes or promoted_edges:
                    print(
                        f"  -> promoted {len(promoted_nodes)} evidence node(s) "
                        f"and {len(promoted_edges)} edge(s) to 'provisional' "
                        f"(live now; onbuild.digest is the override window)."
                    )
            elif choice == "r":
                notes = input("Revision notes (what needs to change): ").strip()
                conn.execute(
                    "UPDATE interview_preps SET human_decision='revise', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (notes or None, prep_id),
                )
                conn.commit()
                revised += 1
            elif choice == "p":
                note = input("Note (optional, why pausing): ").strip()
                conn.execute(
                    "UPDATE interview_preps SET human_decision='pause', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (note or None, prep_id),
                )
                conn.commit()
                paused += 1
            elif choice == "d":
                conn.execute(
                    "UPDATE interview_preps SET human_decision='drop', "
                    "decided_at=datetime('now') WHERE id=?",
                    (prep_id,),
                )
                conn.commit()
                dropped += 1
            else:
                skipped += 1

        print(
            f"\n=== Summary ===\n"
            f"Approve: {approved}\nRevise: {revised}\nPause: {paused}\n"
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
