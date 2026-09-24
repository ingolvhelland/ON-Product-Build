"""
Outcome agent: classifies one captured message about an already-submitted
application. See PRODUCT_BUILD_ANCHOR.md's "Application pipeline, detailed"
section and Log PB-022/PB-027/PB-029 for the design this follows.

Only ever runs against an opportunity whose lifecycle_status is
'submitted_pending_outcome' or 'awaiting_action', OR whose latest
application was approved but never confirmed submitted (PB-049) -
checked in Python before the agent is invoked, not left to the agent to
notice (same discipline as drafting checking a brief's human_decision).
The second case exists because a real reply can arrive before Ingolv
ever runs `onbuild.submission_confirmation`, or he simply moves on after
sending an application and never comes back to confirm it - the message
itself is already proof the submission happened, so it shouldn't be
invisible to this agent just because a separate manual step never ran.

Applies its classification directly to opportunities.lifecycle_status
(PB-038/PB-040's authorship principle): past submission, the fact a
message represents is authored by the recipient, not Ingolv, so
recording it is not a proposal awaiting approval - it's this agent
updating the system to reflect what already happened in the world, the
same way onbuild.submission_confirmation records a fact rather than
judging content. When the application was never confirmed submitted
in the first place, this same principle reaches one step earlier
(PB-049): the message's own arrival backfills `submitted_at` before the
category's own status is applied on top. 'unclear' is the sole
exception - if the message can't be confidently classified, there is no
fact yet to apply, and it waits for a human decision via
`onbuild.outcome_decision`. A human can still correct an already-applied
classification afterward (`onbuild.outcome_decision --override`) - the
exception path, not the norm (PB-002's "action never bypasses human
approval" still governs everything *before* submission; this agent only
ever acts *after* it, or on the message that proves it happened).

Mailbox-access mechanism deliberately not built (PB-027's named boundary,
same reasoning as PB-025's deferred application-portal browser automation):
a message is captured manually (a forwarded email saved to a plain text
file, a pasted screen) and handed to this agent explicitly - there is no
live inbox connection. Building that later would plug into this same input
slot without changing anything else, exactly PB-025's "build room for it,
gate its activation" pattern applied a second time.

No web access, no built-in tools - this agent classifies from what it is
given, the same narrow surface as drafting.
"""

import argparse
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
from dotenv import load_dotenv

from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

SOURCE_TAG = "outcome: message_classification"

# Same module-level-slot pattern as evaluation.py/brief.py/drafting.py
# (PB-026) - set by classify_message() before each run, read by
# propose_evidence_node/propose_evidence_edge's tool handlers.
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
    "Propose new evidence discovered while reading this message - for "
    "example a durable fact about how this organisation actually runs its "
    "process, or something the message reveals about a gap or strength "
    "that would matter for other opportunities too. Always "
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
        origin_artifact_type="outcome",
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
    "nodes discovered while reading this message. Always status='proposed'.",
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
        origin_artifact_type="outcome",
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
    "record_outcome",
    "Record the classification of this message and, for every category "
    "except 'unclear', apply it directly - the recipient authored this "
    "fact, not you, so it updates the opportunity's lifecycle_status "
    "immediately rather than waiting on approval (PB-040). Call this "
    "exactly once, after all reasoning is complete. 'unclear' is the only "
    "category that does NOT get applied - it waits for a human decision "
    "via onbuild.outcome_decision, since there is no real fact yet to "
    "transcribe.",
    {
        "application_id": int,
        "opportunity_id": int,
        "submission_confirmed": bool,
        "category": str,  # 'receipt_confirmation' | 'interview' | 'rejection' | 'further_info' | 'other_request' | 'unclear'
        "rationale": str,
        "suggested_next_step": str,
    },
)
async def record_outcome(args: dict) -> dict:
    category = args["category"]
    outcome_id = evidence_ops.insert_outcome(
        args["application_id"],
        args["opportunity_id"],
        _CURRENT_MESSAGE_TEXT,
        args.get("submission_confirmed"),
        category,
        args["rationale"],
        args.get("suggested_next_step"),
        SOURCE_TAG,
    )

    lines = [
        f"Recorded outcome #{outcome_id} for opportunity "
        f"#{args['opportunity_id']}: category={category}"
    ]
    if category == "unclear":
        lines.append(
            "Category is 'unclear' - no fact to apply yet. Awaiting a "
            "human decision via onbuild.outcome_decision."
        )
    else:
        new_status, backfilled = evidence_ops.apply_outcome(
            outcome_id, args["opportunity_id"], category
        )
        if backfilled:
            lines.append(
                f"Opportunity #{args['opportunity_id']}'s draft had never been "
                f"marked submitted - this message is itself the fact that it "
                f"was, so submission has been backfilled."
            )
        if new_status:
            lines.append(
                f"Applied directly: opportunity #{args['opportunity_id']} "
                f"lifecycle_status -> '{new_status}'."
            )
        else:
            lines.append(
                "Applied directly: no lifecycle_status change needed for "
                "this category (already correct)."
            )
        promoted_nodes, promoted_edges = evidence_ops.promote_provisional_evidence(
            "outcome", args["opportunity_id"]
        )
        if promoted_nodes or promoted_edges:
            lines.append(
                f"-> promoted {len(promoted_nodes)} evidence node(s) and "
                f"{len(promoted_edges)} edge(s) to 'provisional' (live now; "
                f"onbuild.digest is the override window)."
            )
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# Set by classify_message() before each run, read by record_outcome's tool
# handler - same reasoning as drafting.py's _CURRENT_CAPTURED_APPLICATION_TEXT:
# the model is already given this text in its prompt, so there is nothing
# for it to echo back through a tool call.
_CURRENT_MESSAGE_TEXT: str | None = None


OUTCOME_SYSTEM_PROMPT = """\
You are the outcome agent for a personal opportunity-navigation system.
You are given exactly one captured message (a forwarded email, a portal
notification, anything received after an application was submitted) about
exactly one opportunity, and you classify it. You do no research - you
only read and classify. record_outcome applies your classification
directly to the opportunity's state for every category except
'unclear': report exactly what the message says, plainly and
conservatively, because it will take effect immediately, not wait on a
human gate first.

You will be given the opportunity's posting text, the application that was
actually sent (its format assessment, CV, and cover letter/message), and
the captured message text itself.

Call list_evidence_graph once. Only treat "approved" or "provisional" items
as real evidence, in case anything in the message needs checking against
what is actually known (e.g. a claim about something Ingolv said or sent).

Produce these fields for record_outcome:

1. submission_confirmed: true if this message itself is, or contains, a
   confirmation that the application was actually received or is being
   processed (an auto-reply, a portal notification, an explicit
   acknowledgement) - false if the message is silent on receipt, not
   whether receipt happened by some other means you were not told about.

2. category: exactly one of:
   - "receipt_confirmation": the message only confirms receipt/processing,
     with nothing else decision-relevant (a plain automated acknowledgement).
   - "interview": an invitation to interview, a case round, or any further
     stage of the process.
   - "rejection": a plain rejection - no further stage offered.
   - "further_info": a request for more information, documents, or
     clarification before any other decision is made.
   - "other_request": any other action Ingolv needs to take that isn't
     covered above (e.g. a scheduling request, a form to complete).
   - "unclear": the message cannot be confidently classified into any of
     the above from what you were given - use this rather than guessing.
   A single message can both confirm receipt and carry a further category
   (e.g. an automated "received" notice that also asks a screening
   question) - in that case submission_confirmed is true and category
   reflects the further, more decision-relevant content, not
   "receipt_confirmation".

3. rationale: the specific text or signal in the message that supports the
   category you chose - quote or closely paraphrase it, don't just assert
   the category.

4. suggested_next_step: what Ingolv should actually do about this message,
   if anything - concrete and specific (e.g. "reply confirming availability
   for the case round", "no action needed, wait for next contact"). Leave
   this honestly minimal for "receipt_confirmation" and blunt for
   "rejection" (usually: none).

If the message reveals something durable and reusable about how this
organisation or process actually works - not specific to this one
message - propose it via propose_evidence_node/propose_evidence_edge rather
than only noting it in rationale.

Finally, call record_outcome exactly once with all fields plus the
application_id and opportunity_id given to you. Do not write anything
else. Do not draft a reply, do not suggest wording for one beyond the
suggested_next_step summary, and do not change your classification because
a particular category would be more convenient - report what the message
actually says.
"""


def _build_prompt(opportunity_id: int, message_text: str) -> tuple[str, int]:
    opportunity = evidence_ops.fetch_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError(f"No opportunity #{opportunity_id}")
    opp_id, title, organisation, raw_text, source, track = opportunity

    lifecycle_status = evidence_ops.fetch_opportunity_lifecycle_status(opportunity_id)
    awaiting_outcome = lifecycle_status in ("submitted_pending_outcome", "awaiting_action")

    # PB-049: also valid if the draft was approved but never confirmed
    # submitted - this message's own arrival may be the very fact that
    # proves it was (apply_outcome backfills submitted_at in that case).
    _app_id, app_human_decision, app_submitted_at = (
        evidence_ops.fetch_latest_application_decision(opportunity_id) or (None, None, None)
    )
    approved_not_yet_confirmed = (
        lifecycle_status is None
        and app_human_decision == "approve"
        and app_submitted_at is None
    )

    if not (awaiting_outcome or approved_not_yet_confirmed):
        raise ValueError(
            f"Opportunity #{opportunity_id} has lifecycle_status="
            f"{lifecycle_status!r} and its latest application's "
            f"human_decision={app_human_decision!r} - not awaiting an "
            f"outcome and not an approved-but-unconfirmed draft. Nothing "
            f"here for the outcome agent to classify against."
        )

    application = evidence_ops.fetch_latest_application(opportunity_id)
    if application is None:
        raise ValueError(f"No application exists for opportunity #{opportunity_id}")
    (
        application_id,
        tailored_cv,
        cover_letter_or_message,
        application_form_data,
        question_responses,
        portfolio_recommendation,
        human_decision,
        revision_notes,
    ) = application

    prompt = (
        f"OPPORTUNITY #{opp_id}\n"
        f"Title: {title}\n"
        f"Organisation: {organisation}\n\n"
        f"POSTING TEXT:\n{raw_text}\n\n"
        f"APPLICATION #{application_id} ACTUALLY SENT\n"
        f"Cover letter / message:\n{cover_letter_or_message}\n\n"
        f"Tailored CV:\n{tailored_cv}\n\n"
        f"CAPTURED MESSAGE TO CLASSIFY:\n{message_text}"
    )
    return prompt, application_id


async def classify_message(opportunity_id: int, message_text: str) -> None:
    global _CURRENT_OPPORTUNITY_ID, _CURRENT_MESSAGE_TEXT
    init_db()
    prompt, application_id = _build_prompt(opportunity_id, message_text)
    _CURRENT_OPPORTUNITY_ID = opportunity_id
    _CURRENT_MESSAGE_TEXT = message_text

    server = create_sdk_mcp_server(
        name="outcome",
        tools=[
            list_evidence_graph,
            propose_evidence_node,
            propose_evidence_edge,
            record_outcome,
        ],
    )
    options = ClaudeAgentOptions(
        system_prompt=OUTCOME_SYSTEM_PROMPT,
        mcp_servers={"outcome": server},
        strict_mcp_config=True,
        tools=[],
        setting_sources=[],
        allowed_tools=[
            "mcp__outcome__list_evidence_graph",
            "mcp__outcome__propose_evidence_node",
            "mcp__outcome__propose_evidence_edge",
            "mcp__outcome__record_outcome",
        ],
        # Safe by construction: no built-in tools at all, so nothing this
        # agent does reaches anywhere outside this database. Every MCP
        # tool either writes status='proposed' rows (reviewed like
        # everything else) or, for record_outcome, applies a classified
        # message's fact directly to lifecycle_status (PB-040) - real,
        # but bounded to a plain field update on one row this agent was
        # explicitly handed; still never a reply sent anywhere, and
        # nothing before submission is touched by this agent at all.
        permission_mode="bypassPermissions",
        max_turns=50,
    )

    async for message in query(prompt=prompt, options=options):
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify one captured message about a submitted application."
    )
    parser.add_argument("opportunity_id", type=int)
    parser.add_argument(
        "message_path",
        type=Path,
        help="Path to a plain-text file with the captured message content "
        "(a forwarded email, a pasted portal notification, etc.).",
    )
    args = parser.parse_args()

    message_text = args.message_path.read_text()
    asyncio.run(classify_message(args.opportunity_id, message_text))


if __name__ == "__main__":
    main()
