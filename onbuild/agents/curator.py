"""
Curator agent: parses raw material (a CV, later a discovery note) and
proposes structured entries into the saddle. See PRODUCT_BUILD_ANCHOR.md,
"Agent architecture" and Log entries PB-004/PB-006/PB-010 for the reasoning
behind every design choice below.

Everything this agent's tools write is tagged status='proposed'. Nothing it
produces is canonical until a human reviews and approves it separately -
"monitor and judge, not parse and populate" (PB-010). Because every tool here
is architecturally incapable of writing anything beyond a proposal, it is
safe to let the agent call them freely without a per-call permission prompt;
the real gate is the separate review step, not each individual tool call.

This safety argument only holds if the session's tool surface is actually
limited to these tools. See PB-011: an early run left `tools`,
`setting_sources` and `strict_mcp_config` at their defaults, which silently
gave the session the full Claude Code built-in toolset (Bash, Write, Task,
...) plus this machine's ambient skills/agents/MCP servers, auto-approved by
`bypassPermissions`. `allowed_tools` does NOT restrict availability - it
only waives the permission prompt for tools that are already present. The
options below now isolate the session explicitly: `tools=[]` removes every
built-in tool, `setting_sources=[]` stops it loading this machine's
user/project settings, and `strict_mcp_config=True` stops it loading any MCP
server other than the one passed here. Only then is `bypassPermissions` safe.
"""

import argparse
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()  # reads .env into the environment, e.g. ANTHROPIC_API_KEY

SOURCE_TAG = "curator: cv_parse"


@tool(
    "propose_evidence_node",
    "Propose a new evidence item extracted from raw material (a CV, a "
    "document, a discovery note). Always status='proposed' - never "
    "committed directly.",
    {
        "node_type": str,   # 'claim', 'portfolio_artifact', 'gap', or a new type if genuinely needed (PB-006)
        "quality_tag": str,  # direct | transferable | developing | hypothetical | absent_unknown
        "description": str,
        "attributes": str,  # JSON string for type-specific fields, or "" if none
    },
)
async def propose_evidence_node(args: dict) -> dict:
    node_id = evidence_ops.insert_evidence_node(
        args["node_type"],
        args["quality_tag"],
        args["description"],
        args.get("attributes"),
        SOURCE_TAG,
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
    "propose_identity_fact",
    "Propose a fact for the identity/intent core (e.g. salary_floor, "
    "geography, good_fit_definition). Always status='proposed', never "
    "marked current - only an approved fact becomes current (PB-004).",
    {"key": str, "value": str},
)
async def propose_identity_fact(args: dict) -> dict:
    evidence_ops.insert_identity_fact(args["key"], args["value"], SOURCE_TAG)
    return {
        "content": [
            {"type": "text", "text": f"Proposed identity fact: {args['key']}"}
        ]
    }


@tool(
    "list_evidence_graph",
    "List everything currently recorded: every evidence node (any status), "
    "every edge between nodes (any status), and every identity-core fact. "
    "Each item shows its status - proposed, approved, or rejected. "
    "Read-only - writes nothing. Call this once before proposing anything, "
    "so new material can be connected to what already exists instead of "
    "silently duplicating it. A rejected item is not existing coverage - it "
    "means that exact content was tried and judged wrong or wrongly shaped. "
    "If new material corrects a rejected item, propose the corrected version "
    "and link it back with an edge_type like 'corrects', rather than "
    "treating the rejected content as already capturing it. Identity facts "
    "also show current/not current - a key can have more than one approved "
    "row when a fact was revised; only the 'current' one is today's fact, "
    "an older 'not current' row for the same key was superseded, not "
    "contradicted or rejected.",
    {},
)
async def list_evidence_graph(args: dict) -> dict:
    return {"content": [{"type": "text", "text": evidence_ops.fetch_graph_listing()}]}


@tool(
    "propose_evidence_edge",
    "Propose a typed relationship between two evidence nodes that already "
    "exist - either seen via list_evidence_graph or proposed earlier in "
    "this same run. Always status='proposed'. edge_type is a short "
    "descriptive label for the relationship (e.g. 'specifies_subperiod_of', "
    "'elaborates', 'corroborates', 'contradicts', 'supersedes') - the "
    "vocabulary is open, not a fixed list (PB-006).",
    {
        "source_node_id": int,
        "target_node_id": int,
        "edge_type": str,
        "quality_tag": str,  # direct | transferable | developing | hypothetical | absent_unknown
    },
)
async def propose_evidence_edge(args: dict) -> dict:
    edge_id = evidence_ops.insert_evidence_edge(
        args["source_node_id"],
        args["target_node_id"],
        args["edge_type"],
        args["quality_tag"],
        SOURCE_TAG,
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


CURATOR_SYSTEM_PROMPT = """\
You are the curator agent for a personal opportunity-navigation system. You
will be given raw text (a CV, a document, a short discovery note) and must
propose structured entries for it using your tools. You never do anything
else - no commentary, no evaluation of fit, no advice.

Before proposing anything, call list_evidence_graph once to see what is
already recorded. New material - especially a short note, as opposed to a
full CV - often relates to something already there rather than standing
alone: it can specify a sub-period of an existing claim, elaborate it,
corroborate it, contradict it, or supersede it. When it does, propose the
new node as usual, then call propose_evidence_edge to connect it to the
relevant existing node(s), choosing a short descriptive edge_type and
tagging the edge's own quality honestly. Only skip creating a new node
when the incoming text restates something an existing, non-rejected node
already fully captures with no new detail - that is the one case where
there is nothing to add. A rejected node or edge is not existing coverage,
even if its description looks like a match: rejected means that exact
content was already judged wrong or wrongly shaped, usually because it
needs to be split, re-attributed, or corrected. When new material addresses
that, first check whether a different, non-rejected node already covers the
same fact - a node can be rejected simply for adding nothing beyond an
existing node, not only for being wrong. If so, there is nothing to
correct: do not propose a reworded duplicate just because something with
similar content was once rejected. Only propose a corrected version, linked
back with an edge_type like 'corrects', when the underlying fact genuinely
is not captured anywhere else yet. Do not extend the
skip-if-covered rule into filtering by relevance or significance: for
material like a CV, where the person has already decided what belongs,
propose every distinct item exactly as before.

When checking whether something is already covered, read each existing
node's attributes as well as its description - a detail like a date range
is often stored in attributes rather than spelled out in the text, and
missing it there produces an avoidable duplicate node.

If the incoming text's only new information is that two things you can
already see in the graph are related - one caused, informed, or resulted
from the other - do not create a new node to hold that connection. Call
propose_evidence_edge directly between the two existing nodes; the edge
and its quality_tag are the graph's mechanism for recording a discovered
relationship as its own piece of evidence (PB-005), so a connector node
would just duplicate what the edge already does. Only create a new node
when the text adds a fact that is not just the relationship - a new date,
a new detail, a new claim about either side of the connection.

For every distinct claim, accomplishment, or experience in the text, call
propose_evidence_node once. Use node_type 'claim' unless the item is a
standalone work product (a specific painting, build, or publication), in
which case use 'portfolio_artifact'.

Tag quality honestly, not generously:
- direct: the person actually, concretely did this, well-evidenced
- transferable: a real skill, demonstrated in a different context, that
  would need to be applied to a new domain
- developing: in progress, not yet fully demonstrated
- hypothetical: believed possible but not evidenced at all
- absent_unknown: only if explicitly asked to flag a gap - never invent absence

A field-general skill (e.g. stakeholder management, project leadership)
should usually be tagged 'direct' even when the domain it was demonstrated
in differs from other domains you might later compare it against -
directness is about how solid the underlying claim is, not about domain match.

For clear, stable personal facts (salary expectations, location, work
authorization, language ability, explicit statements of what the person is
looking for), call propose_identity_fact instead.

Before proposing an identity fact, check the identity core section of
list_evidence_graph for an existing key that already covers the same
underlying fact - not just an exact key-name match, but the same real-world
thing under a different name (e.g. a key holding a contact email and a new
"the email is now X" statement are the same fact, even with different key
names; a location key and a "use this place for commute calculations" key
are also the same fact if they'd point to the same place). If the new
information revises or replaces what an existing key already holds, reuse
that exact key - the review step already knows how to supersede a key's
previous value correctly. Only introduce a new key when the fact is
genuinely not covered by any existing key, not merely stated in different
words or split into a narrower restatement of one that already exists.

Call each tool once per distinct item. Do not summarize, do not ask
questions, do not produce any other output. When you have proposed
everything in the text, stop.
"""


async def curate_cv(cv_text: str) -> None:
    init_db()
    server = create_sdk_mcp_server(
        name="curator",
        tools=[
            propose_evidence_node,
            propose_identity_fact,
            propose_evidence_edge,
            list_evidence_graph,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=CURATOR_SYSTEM_PROMPT,
        mcp_servers={"curator": server},
        strict_mcp_config=True,  # ignore any other MCP config on this machine
        tools=[],  # no built-in tools (Bash, Write, Task, ...) - MCP tools only
        setting_sources=[],  # isolate from this machine's user/project settings
        allowed_tools=[
            "mcp__curator__propose_evidence_node",
            "mcp__curator__propose_identity_fact",
            "mcp__curator__propose_evidence_edge",
            "mcp__curator__list_evidence_graph",
        ],
        # Safe by construction now that the session's only tools are the two
        # above: every one of them only ever writes status='proposed' rows,
        # never approved/current state. The real gate is the separate review
        # step (PB-010: "monitor and judge, not parse and populate"), not a
        # per-call permission prompt.
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    async for message in query(prompt=cv_text, options=options):
        # Raw visibility while building. The review script (next piece) is
        # the real interface for judging what got proposed.
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the curator agent on a plain-text file."
    )
    parser.add_argument("path", type=Path, help="Path to a text file, e.g. your CV.")
    args = parser.parse_args()
    text = args.path.read_text()
    asyncio.run(curate_cv(text))


if __name__ == "__main__":
    main()
