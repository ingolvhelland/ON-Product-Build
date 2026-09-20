"""
Register-external-application CLI (PB-031) - for an opportunity that was
evaluated, drafted, and already sent entirely outside this system (a
direct application, a portal with no time to route through the pipeline,
anything sent before this system existed or before it was used for it).

This is a fact-recording tool, not a judgment gate - like
`onbuild.submission_confirmation`, it records something Ingolv already
knows and states directly, not a content decision. It exists because
every other path into `applications` assumes the full evaluate -> brief ->
draft pipeline ran first; an externally-sent application has no evaluation
and no real brief, but the schema still requires a `briefs` row for
`applications.brief_id`'s foreign key - a placeholder brief, clearly
marked as such, is inserted rather than skipped. Marks the opportunity
`submitted_pending_outcome` immediately, since in real life it already is
- no separate `onbuild.submission_confirmation` step needed or possible
here (it only looks for applications produced by `onbuild.agents.drafting`).

Run it directly:

    python -m onbuild.register_external_application \\
        --title "..." --organisation "..." [--track "Primary"] \\
        [--posting-file path/to/posting.txt] \\
        [--sent-file path/to/what_was_sent.txt]

Both file flags are optional - omit either to record it honestly as not
captured, rather than blocking registration on having the exact text.
"""

import argparse
from pathlib import Path

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

SOURCE_TAG = "manual: external application (sent outside this system)"


def register(
    title: str,
    organisation: str,
    track: str | None,
    posting_text: str | None,
    sent_content_text: str | None,
) -> tuple[int, int, int]:
    init_db()
    raw_text = posting_text or "(posting text not captured)"
    sent_content_note = sent_content_text or (
        "(content actually sent was not captured - drafted and submitted "
        "outside this system)"
    )
    return evidence_ops.insert_external_application(
        title, organisation, raw_text, track, sent_content_note, SOURCE_TAG
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register an application already sent outside this system."
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--organisation", required=True)
    parser.add_argument("--track", default=None)
    parser.add_argument("--posting-file", type=Path, default=None)
    parser.add_argument("--sent-file", type=Path, default=None)
    args = parser.parse_args()

    posting_text = args.posting_file.read_text() if args.posting_file else None
    sent_content_text = args.sent_file.read_text() if args.sent_file else None

    opportunity_id, brief_id, application_id = register(
        args.title, args.organisation, args.track, posting_text, sent_content_text
    )
    print(
        f"Registered opportunity #{opportunity_id} ({args.title} — "
        f"{args.organisation}), placeholder brief #{brief_id}, "
        f"application #{application_id}. lifecycle_status = "
        f"'submitted_pending_outcome'."
    )


if __name__ == "__main__":
    main()
