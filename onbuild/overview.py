"""
Overview CLI (PB-032) - the admitted-opportunities ranked list PB-022
named and never built, extended with deadline urgency. Terminal-only for
now: this system has been deliberately CLI-first with no running server
(PB-001/PB-003's "3 + 4" execution shape - on-demand work, no always-on
application server) and a real navigation-page interface is a bigger,
separate decision, not made here (see PRODUCT_BUILD_LOG.md PB-032).

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
    print(f"{'#':<4} {'Title':<40} {'Org':<20} {'Status':<24} {'Deadline':<12} {'Score':<6} {'Tier'}")
    print("-" * 120)
    for opp_id, title, organisation, lifecycle_status, deadline, fit_score, fit_tier in rows:
        status = lifecycle_status or "active"
        deadline_display = deadline or "-"
        if deadline and deadline >= today:
            deadline_display = f"{deadline} !"
        print(
            f"{opp_id:<4} {title[:38]:<40} {(organisation or '')[:18]:<20} "
            f"{status:<24} {deadline_display:<12} {fit_score or '-':<6} {fit_tier or '-'}"
        )


if __name__ == "__main__":
    main()
