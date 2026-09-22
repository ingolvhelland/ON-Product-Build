"""
Scanning agent (PB-042) - the last piece of the pipeline named in PB-038's
build order: the "found by scan agent" entry point Ingolv described from
the start, built last because it needed the rest of the pipeline (the
registry, selection, the authorship principle) to exist first, for it to
feed into sensibly.

Two entry points, one shared write path:

- `run_scan()` - the full, unattended scan: reads the evidence graph and
  the highest-`fit_score` evaluations so far (any source - manual entry
  counts exactly as much as a previous scan, Ingolv's own framing: a good
  fit found by hand outside the normal search parameters should shape
  future searches too) as live context for query generation, then
  searches the open web and every active `scan_sources` URL.
- `extract_from_message(text, sender)` - narrower, no web access: given
  one email that didn't match a pending application (most often a "send
  me future opportunities" notice from a company Ingolv already applied
  to), pulls out whatever real postings it actually contains. Called
  directly by `onbuild.mailbox` for exactly this case.

Both ultimately call `record_candidate_opportunity`, which wraps
`evidence_ops.insert_candidate_opportunity`'s dedup check - a posting
already tracked, by any route, is never re-added. Every candidate this
agent finds becomes a plain `opportunities` row with no evaluation and
no track assigned - it does not evaluate, admit, or judge fit in any way
(PB-038's own description of this agent's job: "produces candidate
discovered items... does not evaluate or admit"). track is deliberately
left for `onbuild.agents.evaluation --track` to set later - a human/
evaluation-time call, not a discovery-time guess.

No propose_evidence_node/edge tools here, unlike curator/evaluation/
brief/outcome - this agent's job is finding postings, not building the
evidence graph, so it stays narrow (PB-008/PB-011/PB-012's isolation
discipline applied once more).
"""

import argparse
import asyncio

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "scan: candidate_opportunity"

# Set by extract_from_message() before each run, appended to by
# record_candidate_opportunity's handler - same module-level-slot pattern
# as every other agent's _CURRENT_OPPORTUNITY_ID (PB-026). Lets
# extract_from_message report back what it actually found, since the SDK
# message stream itself isn't structured for a caller to read.
_LAST_RECORDED_IDS: list[int] = []


@tool(
    "list_evidence_graph",
    "List everything currently recorded about the candidate: every "
    "evidence node (any status) and every identity-core fact. Read-only. "
    "Only treat 'approved' or 'provisional' items as real evidence. Call "
    "this to ground search queries in who the candidate actually is, "
    "especially when little or no evaluation history exists yet.",
    {},
)
async def list_evidence_graph(args: dict) -> dict:
    return {"content": [{"type": "text", "text": evidence_ops.fetch_graph_listing()}]}


@tool(
    "list_top_evaluations",
    "List the highest-scoring evaluations recorded so far, across every "
    "opportunity regardless of how it was found - manual entry or a "
    "previous scan. Each shows fit_summary, distinctiveness, and "
    "requirement_matches - use these as a live signal for what to search "
    "for MORE of. Empty or short means little evaluation history exists "
    "yet - lean on list_evidence_graph directly instead of over-narrowing "
    "to a small sample.",
    {},
)
async def list_top_evaluations(args: dict) -> dict:
    rows = evidence_ops.fetch_top_evaluations(limit=5)
    if not rows:
        return {
            "content": [
                {"type": "text", "text": "No evaluations recorded yet."}
            ]
        }
    lines = []
    for title, organisation, fit_summary, distinctiveness, requirement_matches, fit_score, fit_tier in rows:
        lines.append(
            f"- {title} ({organisation or 'unknown org'}) - fit_score={fit_score}, "
            f"fit_tier={fit_tier}\n"
            f"  Fit summary: {fit_summary}\n"
            f"  Distinctiveness: {distinctiveness}\n"
            f"  Requirement matches: {requirement_matches}"
        )
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "list_scan_sources",
    "List every currently active job-board source (label and URL) to "
    "check directly via WebFetch. Read-only.",
    {},
)
async def list_scan_sources(args: dict) -> dict:
    rows = evidence_ops.list_scan_sources(active_only=True)
    if not rows:
        return {
            "content": [
                {"type": "text", "text": "No active scan sources configured."}
            ]
        }
    lines = [f"- {label}: {url}" for _id, label, url, _active in rows]
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "record_candidate_opportunity",
    "Record one distinct job posting you actually found. Call this once "
    "per posting - never invent or guess at one you didn't actually see. "
    "Deduplicated automatically against everything already tracked "
    "(manual entry, a previous scan, or mailbox-fed discovery all count) "
    "- if it's already there, nothing new is recorded and you're told so.",
    {
        "title": str,
        "organisation": str,
        "raw_text": str,  # the posting's own text, as fully and verbatim as possible - not a summary
        "source": str,    # where this was found - a URL, or the search query that surfaced it
        "application_deadline": str,  # ISO date if stated, otherwise ""
    },
)
async def record_candidate_opportunity(args: dict) -> dict:
    opportunity_id = evidence_ops.insert_candidate_opportunity(
        args["title"],
        args.get("organisation") or None,
        args["raw_text"],
        args["source"],
        args.get("application_deadline") or None,
    )
    if opportunity_id is None:
        text = f"Already tracked - '{args['title']}' at '{args.get('organisation')}' matches an existing opportunity. Nothing new recorded."
    else:
        _LAST_RECORDED_IDS.append(opportunity_id)
        text = f"Recorded candidate opportunity #{opportunity_id}: '{args['title']}' at '{args.get('organisation')}'."
    return {"content": [{"type": "text", "text": text}]}


SCAN_SYSTEM_PROMPT = """\
You are the scanning agent for a personal opportunity-navigation system.
Your only job is discovery: find real candidate job postings and record
each one. You never evaluate fit, judge quality, or filter by how good a
match something looks like - that judgment belongs entirely to a later
agent. Surface real candidates; do not pre-select them.

STEP 1 - Ground yourself in who the candidate is.
Call list_evidence_graph once.

STEP 2 - Learn from evaluation history, if any exists.
Call list_top_evaluations. If it returns real results, treat their
fit_summary/distinctiveness/requirement_matches as a live signal for
what to search for MORE of - but do not over-narrow to only that: also
search based on the evidence graph directly, especially when little or
no evaluation history exists yet, so you keep discovering breadth rather
than narrowing only to what has already scored well.

STEP 3 - See what sources are configured.
Call list_scan_sources.

STEP 4 - Search the open web.
Run several distinct WebSearch queries - some informed by what has
scored well so far, some general, based on the evidence graph's own
skills and career narrative directly.

STEP 5 - Check every active source directly.
For each source from list_scan_sources, use WebFetch on its URL. Not
every source will render usefully this way - some sites require a login
or heavy JavaScript and will return nothing useful. If that happens,
just move on; do not guess at content you cannot actually see.

STEP 6 - Record every distinct posting you actually found.
For each one, call record_candidate_opportunity with:
- title: the actual job title
- organisation: the hiring company or organisation's name
- raw_text: the posting's own text, as fully and verbatim as you can
  capture it - not a summary
- source: where you found it (the URL, or the search query that
  surfaced it)
- application_deadline: the ISO date if the posting states one,
  otherwise leave it empty

Do not skip a posting because it seems like a weak fit - that is not
your judgment to make. Do not invent or guess at a posting you did not
actually find. When you have exhausted your searches and sources, stop.
"""


EXTRACT_SYSTEM_PROMPT = """\
You are the scanning agent's mailbox-extraction entry point. You are
given one captured email that did not match any of Ingolv's pending
applications. It is most likely one of two things: a "we'll send you
future opportunities" notice from a company he already applied to (many
ask permission to send these on application), containing one or more
new open positions - or something else entirely (a newsletter, an
unrelated message, spam).

Read the email. If it actually describes one or more distinct job
postings or open positions, call record_candidate_opportunity once for
each one, using the posting's own text (not a summary) for raw_text,
the sender as the source (e.g. "mailbox: <sender>"), and the
application_deadline if one is actually stated.

If the email does not describe any real posting, do not call anything
at all. Do not guess at or invent a posting that is not actually
described in the text.
"""


async def run_scan() -> None:
    init_db()
    server = create_sdk_mcp_server(
        name="scan",
        tools=[
            list_evidence_graph,
            list_top_evaluations,
            list_scan_sources,
            record_candidate_opportunity,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=SCAN_SYSTEM_PROMPT,
        mcp_servers={"scan": server},
        strict_mcp_config=True,
        tools=["WebSearch", "WebFetch"],
        setting_sources=[],
        allowed_tools=[
            "WebSearch",
            "WebFetch",
            "mcp__scan__list_evidence_graph",
            "mcp__scan__list_top_evaluations",
            "mcp__scan__list_scan_sources",
            "mcp__scan__record_candidate_opportunity",
        ],
        # Safe by construction: WebSearch/WebFetch are read-only against
        # the public internet, same argument as brief.py. The only write
        # path, record_candidate_opportunity, always lands as a bare
        # 'candidate' opportunity - no evaluation, no admission, no track
        # - so nothing this agent does moves anything further than any
        # manual entry already could.
        permission_mode="bypassPermissions",
        max_turns=80,
    )

    async for message in query(prompt="Run a full scan for new candidate opportunities.", options=options):
        print(message)


async def extract_from_message(message_text: str, sender: str) -> list[int]:
    global _LAST_RECORDED_IDS
    init_db()
    _LAST_RECORDED_IDS = []
    server = create_sdk_mcp_server(
        name="scan_extract",
        tools=[record_candidate_opportunity],
    )
    options = ClaudeAgentOptions(
        system_prompt=EXTRACT_SYSTEM_PROMPT,
        mcp_servers={"scan_extract": server},
        strict_mcp_config=True,
        tools=[],  # no web access at all - extraction from given text only
        setting_sources=[],
        allowed_tools=["mcp__scan_extract__record_candidate_opportunity"],
        permission_mode="bypassPermissions",
        max_turns=20,
    )

    prompt = f"EMAIL FROM: {sender}\n\n{message_text}"
    async for message in query(prompt=prompt, options=options):
        print(message)

    return list(_LAST_RECORDED_IDS)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan configured sources and the open web for new candidate opportunities."
    )
    parser.parse_args()
    asyncio.run(run_scan())


if __name__ == "__main__":
    main()
