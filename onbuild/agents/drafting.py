"""
Drafting agent: turns an approved brief into actual application material.
Only ever runs against a brief whose human_decision is 'continue' - checked
in Python before the agent is invoked, not left to the agent to notice. See
PRODUCT_BUILD_ANCHOR.md's "Application pipeline, detailed" section and Log
PB-025 for the design this follows.

Unlike brief-writing, drafting does no research of its own - it writes from
what is already known: the brief, the evaluation, the evidence graph, and
whatever was manually captured from the real application (PB-025). Real
application portals routinely ask for things a job posting never mentions -
a "message to the hiring team" instead of a cover letter, per-role
description boxes with room for more than a CV bullet, specific written
questions - and reviewing that real page is a genuine judgment moment for
Ingolv, not something to automate away. `captured_application_text` is
nullable specifically so a draft can still be produced from the posting and
brief alone when nothing was captured, with `application_format_assessment`
saying plainly what's unknown as a result - and it is the same slot a
future automated "probe the application" capability would plug into later
(PB-025's "build room for it, gate its activation" boundary).

No web access needed - drafting only ever writes from material already
gathered, so its tool surface stays exactly as narrow as curator's and
evaluation's (no WebSearch/WebFetch, unlike brief-writing).

Written directly (record_application), like every other agent's output -
the real human gate is the separate approve/revise/drop decision recorded
afterward by `onbuild/application_decision.py`.
"""

import argparse
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "drafting: application_draft"

# Set by write_application() before each run, read by record_application()'s
# tool handler. The model never sees or passes this back through a tool
# call - it is already given the captured text as part of its prompt, so
# there is nothing for it to echo. A module-level slot, not a class or
# closure, only because @tool-decorated functions are looked up by name at
# call time, the same reason SOURCE_TAG above is a module constant rather
# than a per-instance value.
_CURRENT_CAPTURED_APPLICATION_TEXT: str | None = None

# Same module-level-slot reasoning as above, for the opportunity id this
# byproduct evidence should be tagged with (PB-026).
_CURRENT_OPPORTUNITY_ID: int | None = None


@tool(
    "list_evidence_graph",
    "List everything currently recorded: every evidence node (any status), "
    "every edge (any status), and every identity-core fact, each labelled "
    "with its status. Read-only. Only treat 'approved' or 'provisional' "
    "items as real evidence. Identity facts also show current/not current - a key can "
    "have more than one approved row when a fact was revised; only the "
    "'current' one is today's fact, an older 'not current' row for the "
    "same key was superseded, not contradicted or rejected.",
    {},
)
async def list_evidence_graph(args: dict) -> dict:
    return {"content": [{"type": "text", "text": evidence_ops.fetch_graph_listing()}]}


@tool(
    "propose_evidence_node",
    "Propose new evidence discovered while drafting this application - "
    "always status='proposed', reviewed the same way as everything else.",
    {"node_type": str, "quality_tag": str, "description": str, "attributes": str},
)
async def propose_evidence_node(args: dict) -> dict:
    node_id = evidence_ops.insert_evidence_node(
        args["node_type"],
        args["quality_tag"],
        args["description"],
        args.get("attributes"),
        SOURCE_TAG,
        origin_artifact_type="application",
        origin_opportunity_id=_CURRENT_OPPORTUNITY_ID,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Proposed evidence node #{node_id} "
                f"({args['node_type']}, {args['quality_tag']})",
            }
        ]
    }


@tool(
    "propose_evidence_edge",
    "Propose a durable, reusable connection between two existing evidence "
    "nodes discovered while drafting this application. Always "
    "status='proposed'.",
    {
        "source_node_id": int,
        "target_node_id": int,
        "edge_type": str,
        "quality_tag": str,
    },
)
async def propose_evidence_edge(args: dict) -> dict:
    edge_id = evidence_ops.insert_evidence_edge(
        args["source_node_id"],
        args["target_node_id"],
        args["edge_type"],
        args["quality_tag"],
        SOURCE_TAG,
        origin_artifact_type="application",
        origin_opportunity_id=_CURRENT_OPPORTUNITY_ID,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Proposed evidence edge #{edge_id}: "
                f"{args['source_node_id']} --[{args['edge_type']}]--> "
                f"{args['target_node_id']}",
            }
        ]
    }


@tool(
    "record_application",
    "Record the finished draft for this opportunity. Call this exactly "
    "once, after all reasoning is complete. Written directly - generated "
    "content, not evidence; the real human gate is the separate "
    "approve/revise/drop decision recorded afterward.",
    {
        "opportunity_id": int,
        "brief_id": int,
        "application_format_assessment": str,
        "tailored_cv": str,
        "cover_letter_or_message": str,
        "application_form_data": str,
        "question_responses": str,
        "portfolio_recommendation": str,
    },
)
async def record_application(args: dict) -> dict:
    application_id = evidence_ops.insert_application(
        args["opportunity_id"],
        args["brief_id"],
        _CURRENT_CAPTURED_APPLICATION_TEXT,
        args["application_format_assessment"],
        args["tailored_cv"],
        args["cover_letter_or_message"],
        args["application_form_data"],
        args.get("question_responses"),
        args["portfolio_recommendation"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Recorded application #{application_id} for "
                f"opportunity #{args['opportunity_id']}",
            }
        ]
    }


DRAFTING_SYSTEM_PROMPT = """\
You are the drafting agent for a personal opportunity-navigation system.
You produce exactly one application draft for exactly one opportunity whose
brief has already been approved to continue. You do no research of your
own - you write from the brief, the evaluation, the evidence graph, and
whatever was given to you about the real application, exactly as given.

You will be given the opportunity's posting text, its evaluation, its
approved brief, and - if available - text captured from the real
application page (which may show a "message to the hiring team" instead of
a cover letter, per-role or per-degree description boxes, specific written
questions, or other things the posting itself never mentioned). If nothing
was captured, say so plainly in your assessment rather than guessing what
the real form contains.

Call list_evidence_graph once. Only treat "approved" or "provisional" items
as real evidence. Never invent, embellish, or infer facts beyond what is stated or
directly implied by the evidence and its relationships - the same rule
every other agent in this system follows.

Some identity facts exist purely to support this system's own internal
reasoning (e.g. a precise relocation address recorded so commute/proximity
can be calculated against an opportunity) and are not, on that account,
appropriate to put in front of an employer. Never write a precise home or
relocation address on the CV or in the cover letter. If location needs
stating at all - because it bears on a real requirement like work
authorization, commute feasibility, or an on-site expectation - state it at
the country/region level and in terms of the requirement it answers (e.g.
"based in the Copenhagen area; able to meet the five-day on-site
expectation"), not as a street-level or town-level address. A full,
precise address belongs only in application_form_data, and only when a
real captured application form actually has a field asking for one.

Absent real captured application content, the standard, default output is
a cover letter and a CV - nothing more. Do not anticipate a web form's
existence or shape, invent written questions, or manufacture a portfolio
recommendation when nothing in the posting or captured content actually
calls for one. application_form_data, question_responses, and
portfolio_recommendation each have their own instruction below for what to
write in that case - a short, honest statement that nothing calls for that
field yet, not speculative content.

If the posting itself is written in Danish and does not state that English
applications are welcome, default to writing the CV and cover letter in
Danish - assume the employer's own working language applies unless told
otherwise.

If you are given a prior draft and a revision note, this is a revision,
not a fresh draft: read the note as the specific, authoritative statement
of what is wrong, and produce a new version that actually fixes it - do
not simply regenerate from the brief and evidence as if the note did not
exist, and do not reintroduce the flagged content in a slightly different
phrasing. Keep everything about the prior draft that was not flagged.

Produce these fields, in this order of reasoning:

1. application_format_assessment: what this specific application actually
   requires. If real application content was captured, base this on what
   it actually shows - do not assume every application wants a formal
   cover letter and a single CV upload. If nothing was captured, say
   plainly what you don't know (e.g. "no real application page was
   captured; assuming a standard CV plus cover letter upload since nothing
   more specific is known") rather than presenting a guess as fact.

2. tailored_cv: a CV shaped by the brief's strategic_approach - what to
   lead with, how to cover gaps where realistic, what to acknowledge
   openly. Grounded in approved evidence only. This is a different job from
   the general-purpose baseline CV: it may reorder, re-emphasize, or trim
   differently for this specific opportunity.

3. cover_letter_or_message: calibrate length and register to what
   application_format_assessment determined - a full formal cover letter
   when that's what's being asked for, a brief, direct interest message
   when the real application shows something shorter and more informal
   (e.g. a "message to the hiring team" box). Do not default to a long
   formal letter when the real form asks for something else.

4. application_form_data: only produce this when real captured application
   content actually shows form fields to fill, or the posting itself
   describes them. When it applies: a clean, accurate, copy-paste-ready
   extract of personal details (name, contact, location - country/region
   level only, per the address rule above, unless a real captured field
   specifically asks for a full address) and a chronological list of work
   experience and education entries, sourced strictly from approved
   evidence. For each work/education entry, write a fuller description
   than the CV's own bullet - web application forms often have a text box
   with more room than a CV page allows, and a compressed CV bullet
   under-uses that space. Do not just copy the CV's shortened version here.
   When nothing was captured and the posting says nothing about a form,
   write one short line stating there is no known form to extract for
   yet, rather than manufacturing this section speculatively.

5. question_responses: if specific written questions are actually known
   (from the posting text or captured application content), draft honest,
   evidence-grounded answers to each one. If no specific questions are
   known, say so plainly here rather than inventing plausible-sounding
   questions that don't exist.

6. portfolio_recommendation: only recommend specific portfolio_artifact
   evidence nodes when the posting, the brief's strategic_approach, or
   captured application content actually indicates a portfolio submission
   would be welcomed or requested - which nodes are relevant and how to
   present them (a link, a named reference, what to say about it), not
   actual file generation, which this system does not support. Otherwise
   write one short line stating nothing indicates a portfolio submission
   is wanted here, rather than recommending pieces speculatively.

If drafting surfaces a durable, reusable connection in the evidence graph
that neither the evaluation nor the brief already captured, propose it via
propose_evidence_edge (or a new gap via propose_evidence_node) rather than
just asserting it in prose.

Finally, call record_application exactly once with all six fields plus the
opportunity_id and brief_id given to you. Do not write anything else. Do
not submit anything anywhere - this only ever produces a draft for human
review.
"""


def _build_prompt(opportunity_id: int, captured_application_text: str | None) -> tuple[str, int]:
    opportunity = evidence_ops.fetch_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError(f"No opportunity #{opportunity_id}")
    opp_id, title, organisation, raw_text, source, track = opportunity

    brief = evidence_ops.fetch_latest_brief(opportunity_id)
    if brief is None:
        raise ValueError(f"No brief exists for opportunity #{opportunity_id}")
    (
        brief_id,
        company_profile,
        location_presence,
        field_positioning,
        position_fit,
        candidacy_fit_summary,
        strategic_approach,
        human_decision,
        revision_notes,
    ) = brief
    if human_decision != "continue":
        raise ValueError(
            f"Brief #{brief_id} for opportunity #{opportunity_id} has "
            f"human_decision={human_decision!r}, not 'continue' - drafting "
            f"only runs against a brief approved to continue."
        )

    evaluation = evidence_ops.fetch_latest_evaluation(opportunity_id)
    eval_block = "No evaluation on record for this opportunity."
    if evaluation is not None:
        (
            eval_id,
            fit_summary,
            distinctiveness,
            gates_summary,
            countercase,
            requirement_matches,
            gaps_summary,
            fit_score,
            fit_tier,
            suggested_action,
            eval_human_decision,
        ) = evaluation
        eval_block = (
            f"EVALUATION #{eval_id} (fit_score={fit_score}, fit_tier={fit_tier})\n"
            f"Requirement matches: {requirement_matches}\n\n"
            f"Gaps: {gaps_summary or '(none noted)'}"
        )

    captured_block = (
        f"CAPTURED REAL APPLICATION CONTENT:\n{captured_application_text}"
        if captured_application_text
        else "CAPTURED REAL APPLICATION CONTENT: none provided."
    )

    # If the most recent draft for this opportunity was sent back for
    # revision, surface exactly what needs to change plus the prior draft
    # itself - without this, re-running drafting has no way to know it is
    # a revision at all and would just regenerate blindly, likely repeating
    # whatever was flagged. Found and fixed the same day it first mattered
    # for real (Claimlane's cover letter), not deferred.
    revision_block = ""
    prior_application = evidence_ops.fetch_latest_application(opportunity_id)
    if prior_application is not None:
        (
            prior_app_id,
            prior_cv,
            prior_cover_letter,
            prior_form_data,
            prior_question_responses,
            prior_portfolio_recommendation,
            prior_app_human_decision,
            prior_revision_notes,
        ) = prior_application
        if prior_app_human_decision == "revise":
            revision_block = (
                f"\n\nTHIS IS A REVISION of draft #{prior_app_id}, sent back "
                f"with the following note - address it specifically, do not "
                f"silently reintroduce what it flags:\n"
                f"REVISION NOTE: {prior_revision_notes or '(no note given)'}\n\n"
                f"PRIOR DRAFT being revised:\n"
                f"Tailored CV:\n{prior_cv}\n\n"
                f"Cover letter / message:\n{prior_cover_letter}\n\n"
                f"Application form data:\n{prior_form_data}\n\n"
                f"Question responses:\n{prior_question_responses or '(none)'}\n\n"
                f"Portfolio recommendation:\n{prior_portfolio_recommendation}"
            )

    prompt = (
        f"OPPORTUNITY #{opp_id}\n"
        f"Title: {title}\n"
        f"Organisation: {organisation}\n"
        f"Track: {track}\n\n"
        f"POSTING TEXT:\n{raw_text}\n\n"
        f"{eval_block}\n\n"
        f"BRIEF #{brief_id}\n"
        f"Company profile: {company_profile}\n\n"
        f"Location presence: {location_presence or '(none recorded)'}\n\n"
        f"Field positioning: {field_positioning}\n\n"
        f"Position fit: {position_fit}\n\n"
        f"Candidacy fit: {candidacy_fit_summary}\n\n"
        f"Strategic approach: {strategic_approach}\n\n"
        f"{captured_block}"
        f"{revision_block}"
    )
    return prompt, brief_id


async def write_application(
    opportunity_id: int, captured_application_text: str | None = None
) -> None:
    global _CURRENT_CAPTURED_APPLICATION_TEXT, _CURRENT_OPPORTUNITY_ID
    init_db()
    prompt, brief_id = _build_prompt(opportunity_id, captured_application_text)
    _CURRENT_CAPTURED_APPLICATION_TEXT = captured_application_text
    _CURRENT_OPPORTUNITY_ID = opportunity_id

    server = create_sdk_mcp_server(
        name="drafting",
        tools=[
            list_evidence_graph,
            propose_evidence_node,
            propose_evidence_edge,
            record_application,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=DRAFTING_SYSTEM_PROMPT,
        mcp_servers={"drafting": server},
        strict_mcp_config=True,
        tools=[],  # no built-ins at all - drafting does no research of its own
        setting_sources=[],
        allowed_tools=[
            "mcp__drafting__list_evidence_graph",
            "mcp__drafting__propose_evidence_node",
            "mcp__drafting__propose_evidence_edge",
            "mcp__drafting__record_application",
        ],
        # Safe by construction: no built-in tools at all, and every MCP tool
        # either writes status='proposed' rows (reviewed like everything
        # else) or writes an application draft directly - a recommendation
        # for human review, not evidence and not a submission.
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    async for message in query(prompt=prompt, options=options):
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Draft application material for an opportunity whose brief is approved to continue."
    )
    parser.add_argument("opportunity_id", type=int)
    parser.add_argument(
        "--captured-application",
        type=Path,
        default=None,
        help="Path to a plain-text file with whatever was manually captured "
        "from the real application page (a PDF print converted to text, "
        "pasted text, etc.).",
    )
    args = parser.parse_args()

    captured_text = None
    if args.captured_application is not None:
        captured_text = args.captured_application.read_text()

    asyncio.run(write_application(args.opportunity_id, captured_text))


if __name__ == "__main__":
    main()
