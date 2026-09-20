"""
Evaluation agent: assesses one opportunity against the saddle's evidence
graph and identity core, and proposes a fit assessment. See
PRODUCT_BUILD_ANCHOR.md's "Evaluation agent" section and Log PB-009 for the
exact design this follows.

This agent only ever writes two kinds of thing. (1) Its own evaluation
record - a recommendation, not evidence, so it is written directly via
`record_evaluation` rather than through propose/approve. The real human gate
for this stage is the *admission decision* Ingolv records separately
afterward (PB-002's `human_decision` column) - not approval of the
evaluation text itself. (2) New evidence (gap nodes, durable inferred
edges), via the same `propose_evidence_node`/`propose_evidence_edge`
mechanism the curator uses - "has access to the shared discovery-capture
tool, same as every other agent" (PB-009). Both agents' tools call the
shared `onbuild.db.evidence_ops` module rather than one importing the
other's tool functions directly, so each can supply its own correct
`source` tag - see that module's docstring.

Track positioning (saddle component 4, PB-004) does not yet exist as its
own versioned table. For now, track is passed as a plain string at call
time - a known simplification, not the persisted, versioned profile PB-009
actually calls for.

Stateless per call, per the Toolkit invariant (PB-007): every call reads
only the current opportunity and the current graph. No prior evaluation, no
other opportunity, and nothing carried over in the model's own memory is
ever part of its input - PB-009's acceptance test (same inputs, run twice,
same result) depends on this.
"""

import argparse
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "evaluation: opportunity_assessment"

# Set by evaluate_opportunity() before each run, read by propose_evidence_node/
# propose_evidence_edge's tool handlers so byproduct evidence surfaced here
# carries the opportunity it came from (PB-026) - same module-level-slot
# pattern as drafting.py's _CURRENT_CAPTURED_APPLICATION_TEXT, for the same
# reason (a @tool-decorated function is looked up by name at call time, not
# a method with access to instance state).
_CURRENT_OPPORTUNITY_ID: int | None = None


@tool(
    "list_evidence_graph",
    "List everything currently recorded: every evidence node (any status), "
    "every edge (any status), and every identity-core fact, each labelled "
    "with its status. Read-only. Call this once before reasoning about fit. "
    "Only treat 'approved' or 'provisional' items as real evidence - "
    "'proposed' and 'rejected' items are visible but must not be treated as "
    "established fact. Identity facts also show current/not current - a key can have "
    "more than one approved row when a fact was revised; only the "
    "'current' one is today's fact, an older 'not current' row for the "
    "same key was superseded, not contradicted or rejected.",
    {},
)
async def list_evidence_graph(args: dict) -> dict:
    return {"content": [{"type": "text", "text": evidence_ops.fetch_graph_listing()}]}


@tool(
    "propose_evidence_node",
    "Propose new evidence discovered while evaluating this opportunity - "
    "most often a 'gap' node for a requirement with no supporting evidence "
    "at all. Always status='proposed', reviewed the same way as everything "
    "else (PB-009: this agent never writes evidence directly).",
    {
        "node_type": str,
        "quality_tag": str,
        "description": str,
        "attributes": str,
    },
)
async def propose_evidence_node(args: dict) -> dict:
    node_id = evidence_ops.insert_evidence_node(
        args["node_type"],
        args["quality_tag"],
        args["description"],
        args.get("attributes"),
        SOURCE_TAG,
        origin_artifact_type="evaluation",
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
    "Propose a durable, reusable inference discovered while evaluating this "
    "opportunity - a connection between two existing nodes that would hold "
    "generally, not just for this one opportunity. Do not use this for a "
    "match judgment specific only to this posting; that belongs in "
    "requirement_matches on record_evaluation instead. Always "
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
        origin_artifact_type="evaluation",
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
    "record_evaluation",
    "Record the finished evaluation of the current opportunity. Call this "
    "exactly once, after all reasoning (and any propose_evidence_node/"
    "propose_evidence_edge calls) are complete. Written directly - this is "
    "a recommendation, not evidence; the real human gate is the separate "
    "admission decision recorded afterward, not approval of this text.",
    {
        "opportunity_id": int,
        "fit_summary": str,
        "distinctiveness": str,
        "gates_summary": str,
        "countercase": str,
        "requirement_matches": str,  # JSON string: [{"requirement","match_quality","rationale","evidence_node_ids"}]
        "gaps_summary": str,
        "fit_score": int,     # 1-10, ranking aid only - see prompt
        "fit_tier": str,      # 'strong_match' | 'stretch' | 'mismatch'
        "suggested_action": str,  # 'admit' | 'reject' | 'flag'
    },
)
async def record_evaluation(args: dict) -> dict:
    evaluation_id = evidence_ops.insert_evaluation(
        args["opportunity_id"],
        args["fit_summary"],
        args["distinctiveness"],
        args["gates_summary"],
        args["countercase"],
        args["requirement_matches"],
        args.get("gaps_summary"),
        args["fit_score"],
        args["fit_tier"],
        args["suggested_action"],
        SOURCE_TAG,
    )
    return {
        "content": [
            {
                "type": "text",
                "text": f"Recorded evaluation #{evaluation_id} for opportunity "
                f"#{args['opportunity_id']}: "
                f"suggested_action={args['suggested_action']}",
            }
        ]
    }


EVALUATION_SYSTEM_PROMPT = """\
You are the evaluation agent for a personal opportunity-navigation system.
You assess exactly one opportunity against the person's evidence graph and
identity core, and produce a single structured evaluation. You never do
anything else - no drafting, no advice about how to apply, no commentary
beyond the evaluation itself.

You will be given the opportunity's posting text, its source, and the track
this evaluation is being made against. Call list_evidence_graph once before
reasoning about fit. Only treat nodes, edges, and identity facts whose
status is "approved" or "provisional" as real evidence - ignore anything
"proposed" or "rejected" when assessing fit, even though you can see it.

Evidence-node quality (direct/transferable/developing/hypothetical/
absent_unknown) is a property of the node, set once. Per-requirement match
quality is a different question, computed fresh for this opportunity alone,
using the same vocabulary: a field-general skill can be a direct match
regardless of domain, while a field-specific one may be solid evidence of
itself but only a transferable match against a different requirement here.
Never let a node's own quality tag stand in for whether it actually
satisfies a specific requirement in this posting.

Reason about inference over the graph live, at call time - it is not
predefined. If two or more existing nodes together imply a capability that
neither shows alone, and the inference is durable (would hold for
opportunities generally, not just this one), propose it as a new edge via
propose_evidence_edge rather than just asserting it in prose. If you
identify a gap - a requirement with no supporting evidence at all - propose
it as a new node with node_type "gap" via propose_evidence_node when it
seems durable enough to track; otherwise a mention in gaps_summary is
enough.

Your final output must be exactly one call to record_evaluation, with every
field filled honestly:
- fit_summary: the substantive case for or against fit - prose, not a score.
- distinctiveness: what actually differentiates this candidacy for this
  opportunity, if anything - do not pad this if there is nothing distinctive.
- gates_summary: hard, checkable requirements (e.g. work authorization,
  location, language) - state each one's pass/fail/unclear status plainly.
- countercase: the strongest honest argument against admitting this
  opportunity - required even when you believe the fit is strong. A weak or
  perfunctory countercase defeats its purpose.
- requirement_matches: a JSON array, one entry per distinct requirement you
  identify in the posting - {"requirement": "...", "match_quality":
  "direct|transferable|developing|hypothetical|absent_unknown", "rationale":
  "...", "evidence_node_ids": [...]}. A requirement with no evidence still
  gets an entry, tagged absent_unknown.
- gaps_summary: prose summary of what's missing, distinct from any gap
  nodes you proposed - reference them by id if you proposed any.
- fit_score: an integer 1-10. This is a ranking aid only, for sorting
  admitted opportunities against each other on a pending-application list -
  it is not the verdict, and it does not replace or summarize the other
  fields. Score honestly relative to the actual evidence, not relative to
  how you'd like the outcome to read.
- fit_tier: exactly one of "strong_match" (little to reconcile - gaps are
  minor or coverable), "stretch" (genuine, real substantive fit alongside
  serious, named gaps - admitted deliberately so a human can weigh the risk,
  not because the case is actually strong), or "mismatch" (the substance
  itself doesn't fit, not just the surface presentation).
- suggested_action: exactly one of "admit", "reject", or "flag". This must
  follow from fit_tier and from whether the countercase or the case for fit
  is actually stronger - it cannot be decided independently of them:
  - "reject" when the countercase substantively outweighs the case for fit
    (the negative case is the stronger one, not merely present), or when
    fit_tier is "mismatch".
  - "admit" when fit_tier is "strong_match", OR when fit_tier is "stretch"
    and the case for fit is at least as strong as the countercase - a real,
    substantive case with serious gaps is exactly what "stretch" is for,
    and it should still be admitted, clearly labeled, with a correspondingly
    low fit_score, so a human can weigh the risk on a real list rather than
    have it silently filtered out. This system is early enough in its own
    life that a genuine grey-zone case belongs in front of a human, not
    pre-filtered by this agent - reject only the cases that are actually a
    mismatch or actually outweighed, not merely uncertain.
  - "flag" only when there is not enough information to make either call at
    all (e.g. a critical gate cannot be assessed from the graph) - not as a
    default for a merely difficult case.
  This is a recommendation only - the actual admission decision is recorded
  separately, by Ingolv, afterward.

Do not write anything else. Do not draft a brief, a cover letter, or any
application content - that is a later agent's job, only after this
evaluation leads to an actual admission decision.
"""


async def evaluate_opportunity(
    opportunity_id: int, opportunity_text: str, source: str, track: str
) -> None:
    global _CURRENT_OPPORTUNITY_ID
    init_db()
    _CURRENT_OPPORTUNITY_ID = opportunity_id
    server = create_sdk_mcp_server(
        name="evaluation",
        tools=[
            list_evidence_graph,
            propose_evidence_node,
            propose_evidence_edge,
            record_evaluation,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=EVALUATION_SYSTEM_PROMPT,
        mcp_servers={"evaluation": server},
        strict_mcp_config=True,
        tools=[],
        setting_sources=[],
        allowed_tools=[
            "mcp__evaluation__list_evidence_graph",
            "mcp__evaluation__propose_evidence_node",
            "mcp__evaluation__propose_evidence_edge",
            "mcp__evaluation__record_evaluation",
        ],
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    prompt = (
        f"OPPORTUNITY #{opportunity_id}\n"
        f"Track: {track}\n"
        f"Source: {source}\n\n"
        f"{opportunity_text}"
    )

    async for message in query(prompt=prompt, options=options):
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add an opportunity and run the evaluation agent against it."
    )
    parser.add_argument("path", type=Path, help="Path to the opportunity's raw text (job posting).")
    parser.add_argument("--title", required=True)
    parser.add_argument("--organisation", default=None)
    parser.add_argument("--source", default="manual entry")
    parser.add_argument("--track", required=True)
    parser.add_argument(
        "--deadline",
        default=None,
        help="Application deadline, ISO date (YYYY-MM-DD) - PB-032. Omit if the "
        "posting doesn't state one.",
    )
    args = parser.parse_args()

    init_db()
    raw_text = args.path.read_text()
    opportunity_id = evidence_ops.insert_opportunity(
        args.title, args.organisation, raw_text, args.source, args.track, args.deadline
    )
    print(f"Opportunity #{opportunity_id} added.")

    import asyncio

    asyncio.run(evaluate_opportunity(opportunity_id, raw_text, args.source, args.track))


if __name__ == "__main__":
    main()
