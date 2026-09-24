"""
Brief-writing agent: the first point in this system where important outside
information is imported (Ingolv's own framing). Given one opportunity
(already evaluated, admitted, AND selected via `onbuild.selection` - PB-039;
admission alone means "not a bad fit," not "pursue this now"), it
researches the company and the
company's position in the opportunity's field - independently of the
listing itself, so the read isn't anchored to what the posting claims about
itself - then assesses how the specific position fits into both, then
finally pulls the personal saddle to produce a strategic brief: what to
lean into, how to cover gaps where possible, what gaps to acknowledge
openly. See PRODUCT_BUILD_ANCHOR.md's "Application pipeline, detailed"
section and Log PB-023 for the design this follows.

This is the first agent in the system with real web access (WebSearch,
WebFetch) - curator and evaluation are deliberately tool-less beyond their
own MCP tools. The isolation discipline from PB-011/012 still applies: only
these two built-ins are added to `tools`, nothing else (no Bash, Write,
Edit, Task, ...), and `setting_sources`/`strict_mcp_config` still fully
isolate the session. WebSearch/WebFetch are read-only against the public
internet - they write nothing anywhere - so `bypassPermissions` is safe by
the same construction argument as every other tool here.

Company and field-position knowledge is written directly via
save_company_profile/save_company_field_position, not through
propose/approve (Ingolv's own call, PB-023): it is lower-stakes than
misrepresenting Ingolv's own evidence, and self-correcting - a later run
just re-researches and updates it. The brief itself is also written
directly (record_brief), like an evaluation - a strategic document, not
evidence about Ingolv. The real human gate is the separate three-way
decision (continue/revise/drop) recorded afterward by `onbuild/brief_decision.py`.
"""

import argparse
import asyncio

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "brief: opportunity_brief"

# Set by write_brief() before each run - see evaluation.py's identical
# _CURRENT_OPPORTUNITY_ID for why this is a module-level slot (PB-026).
_CURRENT_OPPORTUNITY_ID: int | None = None


@tool(
    "lookup_company_knowledge",
    "Look up whatever is already known about a company, and optionally its "
    "presence at one location and/or its positioning in one field. Call "
    "this before researching anything fresh - reuse persisted knowledge, "
    "only research what's missing or worth refreshing. Read-only.",
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
            presence = evidence_ops.lookup_company_location_presence(
                company_id, location
            )
            if presence:
                pres_id, pres_loc, text, quality_tag, src, upd = presence
                lines.append(
                    f"\nLocation presence #{pres_id} at '{pres_loc}' "
                    f"[{quality_tag}] (last updated {upd}):\n{text}"
                )
            else:
                lines.append(
                    f"\nNo existing presence recorded for location '{location}'."
                )
        field = args.get("field")
        if field:
            position = evidence_ops.lookup_company_field_position(
                company_id, field
            )
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
    "save_company_profile",
    "Save or refresh a company's general profile - researched independently "
    "of any specific opportunity. Overwrites any existing profile for this "
    "company name (companies are looked up by exact name).",
    {"company_name": str, "general_profile": str},
)
async def save_company_profile(args: dict) -> dict:
    company_id = evidence_ops.upsert_company(
        args["company_name"], args["general_profile"], SOURCE_TAG
    )
    return {
        "content": [
            {"type": "text", "text": f"Company profile saved (company_id={company_id})"}
        ]
    }


@tool(
    "save_company_location_presence",
    "Save or refresh a company's presence at one location - researched "
    "independently of any specific opportunity. A multinational's footprint "
    "varies by location as much as by field, so this is tracked separately "
    "from the general profile. quality_tag is 'direct' (explicit evidence, "
    "e.g. a named local office or team) or 'inferred' (reasoned from "
    "indirect evidence because no direct evidence exists).",
    {
        "company_id": int,
        "location": str,
        "presence": str,
        "quality_tag": str,  # 'direct' | 'inferred'
    },
)
async def save_company_location_presence(args: dict) -> dict:
    presence_id = evidence_ops.upsert_company_location_presence(
        args["company_id"],
        args["location"],
        args["presence"],
        args["quality_tag"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Location presence saved (company_location_presence_id={presence_id})",
            }
        ]
    }


@tool(
    "save_company_field_position",
    "Save or refresh a company's positioning within one field - researched "
    "independently of any specific opportunity. quality_tag is 'direct' "
    "(explicit published evidence, e.g. a stated AI strategy) or 'inferred' "
    "(reasoned from indirect evidence because no direct evidence exists).",
    {
        "company_id": int,
        "field": str,
        "positioning": str,
        "quality_tag": str,  # 'direct' | 'inferred'
    },
)
async def save_company_field_position(args: dict) -> dict:
    position_id = evidence_ops.upsert_company_field_position(
        args["company_id"],
        args["field"],
        args["positioning"],
        args["quality_tag"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Field position saved (company_field_position_id={position_id})",
            }
        ]
    }


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
    "Propose new evidence discovered while writing this brief - always "
    "status='proposed', reviewed the same way as everything else.",
    {"node_type": str, "quality_tag": str, "description": str, "attributes": str},
)
async def propose_evidence_node(args: dict) -> dict:
    node_id = evidence_ops.insert_evidence_node(
        args["node_type"],
        args["quality_tag"],
        args["description"],
        args.get("attributes"),
        SOURCE_TAG,
        origin_artifact_type="brief",
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
    "nodes discovered while writing this brief. Always status='proposed'.",
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
        origin_artifact_type="brief",
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
    "record_brief",
    "Record the finished brief for this opportunity. Call this exactly "
    "once, after all steps (company research, location presence, field "
    "research, position fit, candidacy fit and strategy) are complete. "
    "Written directly - a strategic document, not evidence; the real human "
    "gate is the separate continue/revise/drop decision recorded afterward.",
    {
        "opportunity_id": int,
        "company_id": int,
        "company_location_presence_id": int,
        "company_field_position_id": int,
        "company_profile": str,
        "location_presence": str,
        "field_positioning": str,
        "position_fit": str,
        "candidacy_fit_summary": str,
        "strategic_approach": str,
    },
)
async def record_brief(args: dict) -> dict:
    brief_id = evidence_ops.insert_brief(
        args["opportunity_id"],
        args["company_id"],
        args.get("company_location_presence_id"),
        args["company_field_position_id"],
        args["company_profile"],
        args.get("location_presence"),
        args["field_positioning"],
        args["position_fit"],
        args["candidacy_fit_summary"],
        args["strategic_approach"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Recorded brief #{brief_id} for opportunity "
                f"#{args['opportunity_id']}",
            }
        ]
    }


BRIEF_SYSTEM_PROMPT = """\
You are the brief-writing agent for a personal opportunity-navigation
system. You produce exactly one strategic brief for exactly one opportunity
that has already been evaluated and admitted. You never draft application
material yourself - that is a later agent's job, only after this brief is
approved.

Follow these five steps in order. Do not skip ahead - later steps depend on
earlier ones being genuinely independent of the opportunity's own framing.

STEP 1 - Company profile, independent of this opportunity.
Call lookup_company_knowledge for the company name (no location or field
yet). If a profile already exists and looks current, use it as-is unless
something in this opportunity's own context suggests it's stale. If none
exists, research the company using WebSearch/WebFetch - what it does, its
size, market, and general public position - without reading this ahead
through the lens of what the job posting itself claims. Save the result
with save_company_profile. This step must not be shaped by the posting
text.

STEP 2 - Location presence, independent of this opportunity.
Identify the opportunity's actual location (city/country, from the posting
text). A multinational's presence varies by location as much as by field -
a company can be a giant globally and a small local office, or vice versa.
Call lookup_company_knowledge again with that location. If nothing exists,
research the company's presence specifically at that location - a named
local office, team size or function there, how central or peripheral it is
to the company overall. Tag 'direct' if you find explicit evidence (a named
office, local team, local press), 'inferred' if you're reasoning from
indirect signals because no direct evidence exists. Save with
save_company_location_presence. Still independent of the posting's own
framing - this is about the company's actual footprint at this place, not
what the posting claims about the role there.

STEP 3 - Field positioning, independent of this opportunity.
Identify the specific field this opportunity actually belongs to (e.g.
"AI transformation", "biomedical engineering", "sales") - not a generic
label, name what the role is actually about. Call lookup_company_knowledge
again with that field. If nothing exists, research the company's public
position in that field specifically - published strategy documents, product
pages, press, leadership statements. If you find direct evidence (e.g. a
stated AI strategy), tag it 'direct'. If you find nothing direct but can
reason a position from indirect evidence (hiring patterns, product
direction, public statements adjacent to the field), tag it 'inferred' -
say so plainly, do not present an inference as if it were stated fact. Save
with save_company_field_position. This step also must not be shaped by the
posting text - it is about the company and the field, not this role.

STEP 4 - Position fit.
Only now, read the opportunity's actual posting text and its evaluation
(given to you directly). Assess how this specific position fits into the
company generally, into its presence at this location, and into the
field-position from step 3 - is this role central to that positioning, a
peripheral bet, a new direction, a mismatch with what you found? Write this
as `position_fit`.

STEP 5 - Candidacy fit and strategy.
Only now, call list_evidence_graph and read the evaluation's own findings
(fit_summary, requirement_matches, gaps_summary, fit_score, fit_tier -
already given to you). Produce two more fields:
- candidacy_fit_summary: why this candidate is a genuinely good choice for
  this company and this role (and, honestly, whether this company and role
  are a good choice for the candidate too) - grounded in approved evidence,
  building on the evaluation's own findings rather than re-deriving them
  from scratch.
- strategic_approach: what to emphasize for this specific application given
  everything above, how to create coverage for gaps where realistically
  possible, and which gaps should simply be acknowledged openly rather than
  argued around.
If writing this surfaces a durable, reusable connection in the evidence
graph that the evaluation didn't already capture, propose it via
propose_evidence_edge (or a new gap via propose_evidence_node) rather than
just asserting it in prose.

Finally, call record_brief exactly once with all six text fields plus the
opportunity_id, company_id, company_location_presence_id, and
company_field_position_id from steps 1-3. Do not write anything else. Do
not draft a cover letter, CV, or any application content.
"""


def _build_prompt(opportunity_id: int) -> str:
    opportunity = evidence_ops.fetch_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError(f"No opportunity #{opportunity_id}")
    opp_id, title, organisation, raw_text, source, track = opportunity

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
            human_decision,
        ) = evaluation
        eval_block = (
            f"EVALUATION #{eval_id} (fit_score={fit_score}, fit_tier={fit_tier}, "
            f"suggested_action={suggested_action}, human_decision={human_decision})\n"
            f"Fit summary: {fit_summary}\n\n"
            f"Distinctiveness: {distinctiveness}\n\n"
            f"Gates: {gates_summary}\n\n"
            f"Countercase: {countercase}\n\n"
            f"Requirement matches: {requirement_matches}\n\n"
            f"Gaps: {gaps_summary or '(none noted)'}"
        )

    return (
        f"OPPORTUNITY #{opp_id}\n"
        f"Title: {title}\n"
        f"Organisation: {organisation}\n"
        f"Track: {track}\n"
        f"Source: {source}\n\n"
        f"POSTING TEXT:\n{raw_text}\n\n"
        f"{eval_block}"
    )


async def write_brief(opportunity_id: int) -> None:
    global _CURRENT_OPPORTUNITY_ID
    init_db()

    # PB-039: admission alone means "not a bad fit" - it does not mean
    # "pursue this now." Brief-writing requires both, checked here in
    # addition to (not instead of) the pipeline registry's own read-only
    # view of the same fact (PB-038's defense-in-depth principle).
    evaluation_decision, selected_at = evidence_ops.fetch_selection_status(opportunity_id)
    if evaluation_decision != "admit":
        raise ValueError(
            f"Opportunity #{opportunity_id} is not admitted (human_decision="
            f"{evaluation_decision!r}). Run onbuild.admission first."
        )
    if selected_at is None:
        raise ValueError(
            f"Opportunity #{opportunity_id} is admitted but not yet selected. "
            f"Run onbuild.selection first to choose it from the ranked list."
        )

    _CURRENT_OPPORTUNITY_ID = opportunity_id
    prompt = _build_prompt(opportunity_id)

    server = create_sdk_mcp_server(
        name="brief",
        tools=[
            lookup_company_knowledge,
            save_company_profile,
            save_company_location_presence,
            save_company_field_position,
            list_evidence_graph,
            propose_evidence_node,
            propose_evidence_edge,
            record_brief,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=BRIEF_SYSTEM_PROMPT,
        mcp_servers={"brief": server},
        strict_mcp_config=True,  # ignore any other MCP config on this machine
        tools=["WebSearch", "WebFetch"],  # only built-ins: real research, nothing else
        setting_sources=[],  # isolate from this machine's user/project settings
        allowed_tools=[
            "WebSearch",
            "WebFetch",
            "mcp__brief__lookup_company_knowledge",
            "mcp__brief__save_company_profile",
            "mcp__brief__save_company_location_presence",
            "mcp__brief__save_company_field_position",
            "mcp__brief__list_evidence_graph",
            "mcp__brief__propose_evidence_node",
            "mcp__brief__propose_evidence_edge",
            "mcp__brief__record_brief",
        ],
        # Safe by construction: the only built-ins present are WebSearch and
        # WebFetch, both read-only against the public internet - they write
        # nothing anywhere. Every MCP tool here either writes status=
        # 'proposed' rows (reviewed like everything else) or writes company/
        # brief rows directly, per Ingolv's own call in PB-023 that this is
        # lower-stakes and self-correcting, unlike evidence about him.
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    async for message in query(prompt=prompt, options=options):
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a brief for an already-evaluated, admitted opportunity."
    )
    parser.add_argument("opportunity_id", type=int)
    args = parser.parse_args()
    asyncio.run(write_brief(args.opportunity_id))


if __name__ == "__main__":
    main()
