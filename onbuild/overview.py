"""
Overview CLI (PB-032) - the admitted-opportunities ranked list PB-022
named and never built, extended with deadline urgency and, since PB-036,
a "pending" flag when a brief, application, or interview prep is sitting
paused for that opportunity - the same foregrounding purpose as the
deadline sort: a paused decision with a real deadline attached shouldn't
need to be remembered.

Runs `evidence_ops.close_expired_opportunities()` first - deterministic,
not a judgment (a deadline having passed with nothing submitted is a
plain fact), so it happens automatically every time this is run rather
than needing its own gate or a scheduled job.

Run it directly:

    python -m onbuild.overview
"""

from datetime import date

from onbuild.db import evidence_ops


def main() -> None:
    closed_ids = evidence_ops.close_expired_opportunities()
    if closed_ids:
        print(f"Closed {len(closed_ids)} opportunity(ies) past deadline: "
              f"{', '.join(f'#{i}' for i in closed_ids)}\n")

    rows = evidence_ops.fetch_overview()
    if not rows:
        print("No admitted opportunities currently active or awaiting outcome.")
        return

    today = date.today().isoformat()
    print(f"{'#':<4} {'Title':<38} {'Org':<18} {'Status':<24} {'Deadline':<12} {'Score':<6} {'Tier':<14} {'Pending'}")
    print("-" * 140)
    for (
        opp_id, title, organisation, lifecycle_status, deadline, fit_score, fit_tier,
        latest_brief_decision, latest_application_decision, latest_interview_prep_decision,
    ) in rows:
        status = lifecycle_status or "active"
        deadline_display = deadline or "-"
        if deadline and deadline >= today:
            deadline_display = f"{deadline} !"

        paused_gates = []
        if latest_brief_decision == "pause":
            paused_gates.append("brief")
        if latest_application_decision == "pause":
            paused_gates.append("application")
        if latest_interview_prep_decision == "pause":
            paused_gates.append("interview_prep")
        pending_display = f"PAUSED: {', '.join(paused_gates)}" if paused_gates else "-"

        print(
            f"{opp_id:<4} {title[:36]:<38} {(organisation or '')[:16]:<18} "
            f"{status:<24} {deadline_display:<12} {fit_score or '-':<6} "
            f"{(fit_tier or '-'):<14} {pending_display}"
        )


if __name__ == "__main__":
    main()
