"""
Hold-opportunity CLI (PB-037) - for an opportunity that is genuinely still
live but not currently actionable for an external reason: a posting that
reappeared but whose portal isn't open yet, a clarification requested and
pending, anything where the right answer isn't "dead" (`closed`) but also
isn't "ready to act on" (active). A fact update, not a judgment - same
reasoning as `onbuild.submission_confirmation`.

Distinct from `closed`: `close_expired_opportunities()` never touches an
`on_hold` opportunity (it has no deadline to expire in the first place,
by construction - the whole point of "on hold" is that none is known
yet). `onbuild.relist_opportunity` is what moves it back to active, once
a real deadline is actually known.

Run it directly:

    python -m onbuild.hold_opportunity <opportunity_id> "<note>"

The note is free text - why it's on hold, so the reason isn't lost by the
time it's revisited. Optional, but strongly recommended: `on_hold`'s whole
point is that the reason is external and easy to forget.
"""

import argparse

from onbuild.db import evidence_ops


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Put an opportunity on hold - live but not currently actionable."
    )
    parser.add_argument("opportunity_id", type=int)
    parser.add_argument("note", nargs="?", default=None, help="Why it's on hold (optional but recommended).")
    args = parser.parse_args()

    evidence_ops.put_opportunity_on_hold(args.opportunity_id, args.note)
    print(f"Opportunity #{args.opportunity_id}: lifecycle_status set to 'on_hold'.")
    if args.note:
        print(f"Note: {args.note}")


if __name__ == "__main__":
    main()
