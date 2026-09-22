"""
Close-opportunity CLI (PB-041) - for an opportunity Ingolv has decided is
dead for a reason other than its deadline passing (the posting is gone,
the role's been filled, it's simply no longer worth tracking). A fact
update, not a content judgment - same reasoning as
`onbuild.hold_opportunity` and `onbuild.submission_confirmation`.

Distinct from `close_expired_opportunities()` (PB-032), which only ever
closes an active opportunity whose captured deadline has actually
passed - that's a deterministic fact needing no gate. This tool is for
the case that isn't deducible from a date: Ingolv's own read that a
position is over. Only works from 'active' (NULL) or 'on_hold' - it
refuses to overwrite a real recorded outcome (rejected, interview,
awaiting_action, submitted_pending_outcome) or an already-closed one,
since those are facts about what actually happened, not something to
silently replace.

Run it directly:

    python -m onbuild.close_opportunity <opportunity_id> "<note>"

The note is free text - why it's being closed. Optional, but
recommended for the same reason `hold_opportunity`'s is: it's easy to
forget later why a given opportunity was dropped.
"""

import argparse

from onbuild.db import evidence_ops


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually close an opportunity that's dead for a reason other than its deadline passing."
    )
    parser.add_argument("opportunity_id", type=int)
    parser.add_argument("note", nargs="?", default=None, help="Why it's being closed (optional but recommended).")
    args = parser.parse_args()

    evidence_ops.close_opportunity(args.opportunity_id, args.note)
    print(f"Opportunity #{args.opportunity_id}: lifecycle_status set to 'closed'.")
    if args.note:
        print(f"Note: {args.note}")


if __name__ == "__main__":
    main()
