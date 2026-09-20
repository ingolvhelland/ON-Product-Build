"""
Relist-opportunity CLI (PB-032) - reopens an opportunity that
`evidence_ops.close_expired_opportunities()` closed because its deadline
passed with nothing submitted, when the same posting reappears with a new
deadline. A fact update, not a judgment - same reasoning as
`onbuild.submission_confirmation`.

Only actually reopens something that is currently `lifecycle_status='closed'`
- setting a deadline on an opportunity in any other state (already
submitted, already rejected, etc.) just updates the date, it never
resurrects a state that wasn't 'closed' to begin with.

Run it directly:

    python -m onbuild.relist_opportunity <opportunity_id> <new_deadline YYYY-MM-DD>
"""

import argparse

from onbuild.db import evidence_ops


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reopen a closed opportunity with a new application deadline."
    )
    parser.add_argument("opportunity_id", type=int)
    parser.add_argument("new_deadline", help="ISO date, YYYY-MM-DD")
    args = parser.parse_args()

    evidence_ops.relist_opportunity(args.opportunity_id, args.new_deadline)
    print(
        f"Opportunity #{args.opportunity_id}: application_deadline set to "
        f"{args.new_deadline}. Reopened if it was 'closed'."
    )


if __name__ == "__main__":
    main()
