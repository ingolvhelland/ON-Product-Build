"""
Resolve-unmatched CLI (PB-031) - for a message `onbuild.mailbox` already
captured in `unmatched_mailbox_messages` (zero or multiple candidate
matches at the time) that Ingolv has since identified by hand, most often
because the opportunity it belongs to did not exist in the system yet.

Runs the outcome agent directly against the already-captured message text
- no need to re-fetch anything live - then marks the row resolved so it
does not need looking at again. Does not itself decide anything about the
message's content: that stays with `onbuild.agents.outcome`'s own
classification and, afterward, `onbuild.outcome_decision`'s human gate,
exactly as if the message had matched live in the first place.

Run it directly:

    python -m onbuild.resolve_unmatched <unmatched_id> <opportunity_id>
"""

import argparse
import asyncio

from onbuild.agents import outcome
from onbuild.db import evidence_ops


async def resolve(unmatched_id: int, opportunity_id: int) -> None:
    row = evidence_ops.fetch_unmatched_message(unmatched_id)
    if row is None:
        raise ValueError(f"No unmatched message #{unmatched_id}")
    _, uid, sender, subject, message_text, status = row
    if status != "unresolved":
        raise ValueError(f"Unmatched message #{unmatched_id} is already {status!r}")

    print(f"Resolving unmatched message #{unmatched_id} (UID {uid}, {subject!r} from {sender!r}) "
          f"against opportunity #{opportunity_id}...")
    await outcome.classify_message(opportunity_id, message_text)
    evidence_ops.resolve_unmatched_message(unmatched_id)
    print(f"Marked unmatched message #{unmatched_id} resolved.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify an already-captured unmatched message against a known opportunity."
    )
    parser.add_argument("unmatched_id", type=int)
    parser.add_argument("opportunity_id", type=int)
    args = parser.parse_args()
    asyncio.run(resolve(args.unmatched_id, args.opportunity_id))


if __name__ == "__main__":
    main()
