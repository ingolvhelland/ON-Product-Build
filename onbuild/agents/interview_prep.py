"""
Interview-preparation agent: resolves PB-027's placement (its own agent,
not a mode of drafting) with the actual design Ingolv specified (PB-035).
Only runs once an opportunity has reached lifecycle_status='interview' -
checked in Python before the agent is invoked, same discipline as
drafting checking a brief's human_decision.

Pulls the approved brief as its starting point (not re-derived from
scratch, PB-023's own discipline applied again) plus the application
that was actually submitted, then does two kinds of fresh research the
brief never did: who the interview is actually scheduled with (read
directly from the real interview-invitation message, not guessed), and
who runs the office/location the position sits in - a different question
from company-location *presence* (already covered by brief-writing) since
this is specifically about named people, not general footprint.

The second agent in this system with real web access (WebSearch,
WebFetch), after brief-writing (PB-023) - same isolation discipline:
only these two built-ins, nothing else, `setting_sources`/
`strict_mcp_config` fully isolating the session, `bypassPermissions` safe
by the same construction argument (every MCP tool here writes either a
status='proposed' row or a direct recommendation, never anything
canonical; WebSearch/WebFetch write nothing anywhere).
"""

import argparse
import asyncio

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "interview_prep: interview_preparation"

# Same module-level-slot pattern as evaluation.py/brief.py/drafting.py/
# outcome.py (PB-026).
_CURRENT_OPPORTUNITY_ID: int | None = None


@tool(
    "lookup_company_knowledge",
    "Look up whatever is already known about a company, and optionally its "
    "presence at one location and/or its positioning in one field - reuse "
    "what brief-writing already researched rather than re-researching the "
    "company itself. Read-only.",
    {"company_name": str, "location": str, "field": str},
)
async def lookup_company_knowledge(args: dict) -> dict:
    company = evidence_ops.lookup_company(args["company_name"])
    lines = []
    if company:
        company_id, name, general_profile, source, updated_at = company
        lines.append(
            f"Company #{company_id} '{name}' (last updated {updated_at}):\n"
            f"{general_profile}"
        )
        location = args.get("location")
        if location:
            presence = evidence_ops.lookup_company_location_presence(company_id, location)
            if presence:
                pres_id, pres_loc, text, quality_tag, src, upd = presence
                lines.append(
                    f"\nLocation presence #{pres_id} at '{pres_loc}' "
                    f"[{quality_tag}] (last updated {upd}):\n{text}"
                )
            else:
                lines.append(f"\nNo existing presence recorded for location '{location}'.")
        field = args.get("field")
        if field:
            position = evidence_ops.lookup_company_field_position(company_id, field)
            if position:
                pos_id, pos_field, positioning, quality_tag, src, upd = position
                lines.append(
                    f"\nField position #{pos_id} in '{pos_field}' "
                    f"[{quality_tag}] (last updated {upd}):\n{positioning}"
                )
            else:
                lines.append(f"\nNo existing position recorded for field '{field}'.")
    else:
        lines.append(f"No existing knowledge of company '{args['company_name']}'.")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "list_evidence_graph",
    "List currently approved/provisional evidence nodes and edges, plus "
    "every current identity-core fact. Read-only. Proposed and rejected "
    "items, and identity facts already superseded, are intentionally "
    "left out of this view (PB-051 - keeps the listing a manageable size "
    "as the graph grows; none of it was ever real evidence for your "
    "purposes anyway). Descriptions are shown in short form, not their "
    "full original text.",
    {},
)
async def list_evidence_graph(args: dict) -> dict:
    return {"content": [{"type": "text", "text": evidence_ops.fetch_graph_listing()}]}


@tool(
    "propose_evidence_node",
    "Propose new evidence discovered while preparing for this interview - "
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
        origin_artifact_type="interview_prep",
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
    "nodes discovered while preparing for this interview. Always "
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
        origin_artifact_type="interview_prep",
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
    "record_interview_prep",
    "Record the finished interview preparation for this opportunity. Call "
    "this exactly once, after all research and reasoning is complete. "
    "Written directly - a strategy document, not evidence; the real human "
    "gate is the separate approve/revise/drop decision recorded afterward.",
    {
        "opportunity_id": int,
        "brief_id": int,
        "interviewer_research": str,
        "office_leadership_research": str,
        "talking_points": str,
        "requirement_coverage": str,  # JSON string: [{"requirement","coverage","evidence_node_ids","note"}]
    },
)
async def record_interview_prep(args: dict) -> dict:
    prep_id = evidence_ops.insert_interview_prep(
        args["opportunity_id"],
        args["brief_id"],
        _CURRENT_OUTCOME_ID,
        args["interviewer_research"],
        args["office_leadership_research"],
        args["talking_points"],
        args["requirement_coverage"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Recorded interview prep #{prep_id} for opportunity "
                f"#{args['opportunity_id']}",
            }
        ]
    }


# Set by prepare_interview() before each run, read by record_interview_prep's
# tool handler - same reasoning as drafting.py's captured-text slot.
_CURRENT_OUTCOME_ID: int | None = None


INTERVIEW_PREP_SYSTEM_PROMPT = """\
You are the interview-preparation agent for a personal opportunity-navigation
system. You are given one opportunity that has reached the interview stage,
its approved brief, the application that was actually submitted, the real
interview-invitation message, and the evidence graph. You produce a
strategy document for the interview itself - not a rewrite of the brief or
the application.

Call lookup_company_knowledge first, for the company (and its location and
field, if the brief already named them) - reuse what brief-writing already
researched about the company itself rather than re-researching it. Call
list_evidence_graph once. Only treat "approved" or "provisional" evidence as
real - never invent, embellish, or infer facts beyond what is stated or
directly implied.

Produce these fields, in this order of reasoning:

1. interviewer_research: read the real interview-invitation message given
   to you and identify who the interview is actually scheduled with - every
   named person, not just the first one mentioned. For each, research their
   professional background (WebSearch/WebFetch) - their role, what they
   likely care about given that role, anything genuinely useful for tailoring
   how the conversation goes. If the invitation names no one specifically,
   say so plainly rather than inventing a name to research.

2. office_leadership_research: research who actually runs the specific
   office or location this position is set in - the site/office lead, not
   necessarily the same person as the interviewer(s) above. This is
   different from the company's general location presence (already covered
   by the brief) - it is about named people in charge of that specific
   office, useful context even if they are never in the room.

3. talking_points: exactly 5-7 talking points, drawing on both the approved
   brief's strategic_approach and what was actually written in the
   submitted application (stay consistent with what was already sent, don't
   introduce a disconnected new angle). Each point must be a concrete
   strategy for bringing a specific, named piece of evidence into the
   conversation explicitly - not a vague theme. Reference the evidence node
   id(s) each point is grounded in.

4. requirement_coverage: go back to the original job posting's stated
   requirements (the same ones the evaluation already assessed - use its
   requirement_matches as your starting reference, don't re-derive match
   quality from scratch, but do incorporate any evidence approved since
   then). For each requirement, produce a JSON array entry:
   {"requirement": "...", "coverage": "direct" | "indirect" | "none",
   "evidence_node_ids": [...], "note": "how to actually say this out loud
   if asked"}. "direct" means the evidence speaks straight to the
   requirement; "indirect" means it's a genuine but non-obvious connection
   worth explaining; "none" means honestly say so rather than stretching a
   thin connection to look complete.

Finally, call record_interview_prep exactly once with all four fields plus
the opportunity_id and brief_id given to you. Do not write anything else. Do
not draft interview answers verbatim or a script - talking points and
strategy, not a transcript.
"""


def _build_prompt(opportunity_id: int) -> tuple[str, int, int | None]:
    opportunity = evidence_ops.fetch_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError(f"No opportunity #{opportunity_id}")
    opp_id, title, organisation, raw_text, source, track = opportunity

    lifecycle_status = evidence_ops.fetch_opportunity_lifecycle_status(opportunity_id)
    if lifecycle_status != "interview":
        raise ValueError(
            f"Opportunity #{opportunity_id} has lifecycle_status="
            f"{lifecycle_status!r}, not 'interview' - interview preparation "
            f"only runs once onbuild.outcome_decision has confirmed an "
            f"interview invitation for this opportunity."
        )

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
        brief_human_decision,
        brief_revision_notes,
    ) = brief
    if brief_human_decision != "continue":
        raise ValueError(
            f"Brief #{brief_id} for opportunity #{opportunity_id} has "
            f"human_decision={brief_human_decision!r}, not 'continue'."
        )

    evaluation = evidence_ops.fetch_latest_evaluation(opportunity_id)
    eval_block = "No evaluation on record for this opportunity."
    if evaluation is not None:
        (
            eval_id, fit_summary, distinctiveness, gates_summary, countercase,
            requirement_matches, gaps_summary, fit_score, fit_tier,
            suggested_action, eval_human_decision,
        ) = evaluation
        eval_block = (
            f"EVALUATION #{eval_id}'s requirement_matches (starting reference "
            f"for requirement_coverage - do not re-derive match quality from "
            f"scratch):\n{requirement_matches}"
        )

    application = evidence_ops.fetch_latest_application(opportunity_id)
    app_block = "No application on record for this opportunity."
    if application is not None:
        (
            app_id, tailored_cv, cover_letter_or_message, application_form_data,
            question_responses, portfolio_recommendation, app_human_decision,
            app_revision_notes,
        ) = application
        app_block = (
            f"APPLICATION #{app_id} ACTUALLY SUBMITTED\n"
            f"Cover letter / message:\n{cover_letter_or_message}\n\n"
            f"Tailored CV:\n{tailored_cv}"
        )

    interview_outcome = evidence_ops.fetch_confirmed_interview_outcome(opportunity_id)
    outcome_id = None
    invitation_block = "No interview-invitation message on record."
    if interview_outcome is not None:
        outcome_id, _application_id, captured_message_text = interview_outcome
        invitation_block = f"REAL INTERVIEW-INVITATION MESSAGE:\n{captured_message_text}"

    prompt = (
        f"OPPORTUNITY #{opp_id}\n"
        f"Title: {title}\n"
        f"Organisation: {organisation}\n\n"
        f"POSTING TEXT (original requirements):\n{raw_text}\n\n"
        f"{eval_block}\n\n"
        f"BRIEF #{brief_id}\n"
        f"Company profile: {company_profile}\n\n"
        f"Location presence: {location_presence or '(none recorded)'}\n\n"
        f"Field positioning: {field_positioning}\n\n"
        f"Position fit: {position_fit}\n\n"
        f"Candidacy fit: {candidacy_fit_summary}\n\n"
        f"Strategic approach: {strategic_approach}\n\n"
        f"{app_block}\n\n"
        f"{invitation_block}"
    )
    return prompt, brief_id, outcome_id


async def prepare_interview(opportunity_id: int) -> None:
    global _CURRENT_OPPORTUNITY_ID, _CURRENT_OUTCOME_ID
    init_db()
    prompt, brief_id, outcome_id = _build_prompt(opportunity_id)
    _CURRENT_OPPORTUNITY_ID = opportunity_id
    _CURRENT_OUTCOME_ID = outcome_id

    server = create_sdk_mcp_server(
        name="interview_prep",
        tools=[
            lookup_company_knowledge,
            list_evidence_graph,
            propose_evidence_node,
            propose_evidence_edge,
            record_interview_prep,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=INTERVIEW_PREP_SYSTEM_PROMPT,
        mcp_servers={"interview_prep": server},
        strict_mcp_config=True,
        tools=["WebSearch", "WebFetch"],
        setting_sources=[],
        allowed_tools=[
            "WebSearch",
            "WebFetch",
            "mcp__interview_prep__lookup_company_knowledge",
            "mcp__interview_prep__list_evidence_graph",
            "mcp__interview_prep__propose_evidence_node",
            "mcp__interview_prep__propose_evidence_edge",
            "mcp__interview_prep__record_interview_prep",
        ],
        # Safe by construction: the only built-ins are WebSearch and
        # WebFetch, both read-only against the public internet - they
        # write nothing anywhere. Every MCP tool here either writes
        # status='proposed' rows (reviewed like everything else) or
        # writes a strategy document directly - a recommendation for
        # human review, never evidence and never anything sent anywhere.
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    prompt_with_id = f"opportunity_id={opportunity_id}, brief_id={brief_id}\n\n{prompt}"

    async for message in query(prompt=prompt_with_id, options=options):
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare interview strategy for an opportunity at the interview stage."
    )
    parser.add_argument("opportunity_id", type=int)
    args = parser.parse_args()
    asyncio.run(prepare_interview(args.opportunity_id))


if __name__ == "__main__":
    main()
