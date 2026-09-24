"""
Brief-decision CLI - the human gate over a written brief (PB-022, PB-023,
PB-036).

Records Ingolv's actual decision - continue, revise, pause, or drop - for
each opportunity's most recent brief. A brief can be good enough to act
on, need real rework before it is, be genuinely good but not the right
moment to commit to it, or reveal that the opportunity isn't worth
pursuing after all.

'pause' (PB-036) is not a fourth judgment about quality - it means "I
haven't decided yet, on purpose." A paused brief keeps reappearing here on
every future run, sorted by the opportunity's own application_deadline
(soonest first, shown alongside each item) so a paused decision with a
real deadline approaching surfaces on its own rather than needing to be
remembered - the same reasoning `onbuild.overview` sorts by deadline
urgency (PB-032). It stays paused, with the same four options available,
until a real decision (continue/revise/drop) is made.

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

from onbuild.db import evidence_ops
from onbuild.db.schema import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _prompt() -> str:
    while True:
        choice = (
            input("[c]ontinue / [r]evise / [p]ause / [d]rop / [s]kip / [q]uit > ")
            .strip()
            .lower()
        )
        if choice in ("c", "r", "p", "d", "s", "q"):
            return choice
        print("Please enter c, r, p, d, s, or q.")


def pending_decisions(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT b.id, b.opportunity_id, o.title, o.organisation, o.track,
               o.application_deadline, o.source, b.company_profile, b.field_positioning,
               b.position_fit, b.candidacy_fit_summary, b.strategic_approach,
               b.human_decision
        FROM briefs b
        JOIN opportunities o ON o.id = b.opportunity_id
        WHERE b.id IN (SELECT MAX(id) FROM briefs GROUP BY opportunity_id)
          AND (b.human_decision IS NULL OR b.human_decision = 'pause')
        ORDER BY
            CASE WHEN o.application_deadline IS NULL THEN 1 ELSE 0 END,
            o.application_deadline ASC,
            b.id
        """
    ).fetchall()


def main() -> None:
    conn = _connect()
    try:
        rows = pending_decisions(conn)
        if not rows:
            print("No pending brief decisions.")
            return

        continued = revised = paused = dropped = skipped = 0
        for (
            brief_id,
            opp_id,
            title,
            organisation,
            track,
            application_deadline,
            source,
            company_profile,
            field_positioning,
            position_fit,
            candidacy_fit_summary,
            strategic_approach,
            prior_decision,
        ) in rows:
            paused_tag = " [PAUSED - revisit]" if prior_decision == "pause" else ""
            deadline_tag = f" | deadline: {application_deadline}" if application_deadline else ""
            print(
                f"\n=== Opportunity #{opp_id}: {title} — "
                f"{organisation or '(no organisation)'} [{track}]{deadline_tag}{paused_tag} ==="
            )
            print(f"Brief #{brief_id}")
            print(f"Posting: {source or '(no source recorded)'}")
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
                # PB-026: continuing this brief is the accepting decision
                # that lets any byproduct evidence it surfaced go live as
                # 'provisional', pending onbuild.digest.
                promoted_nodes, promoted_edges = (
                    evidence_ops.promote_provisional_evidence("brief", opp_id)
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
                    "UPDATE briefs SET human_decision='revise', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (notes or None, brief_id),
                )
                conn.commit()
                revised += 1
            elif choice == "p":
                note = input("Note (optional, why pausing): ").strip()
                conn.execute(
                    "UPDATE briefs SET human_decision='pause', "
                    "revision_notes=?, decided_at=datetime('now') WHERE id=?",
                    (note or None, brief_id),
                )
                conn.commit()
                paused += 1
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
            f"Continue: {continued}\nRevise: {revised}\nPause: {paused}\n"
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
