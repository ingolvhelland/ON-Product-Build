"""
Pipeline registry (PB-038) - the single, explicit definition of the
opportunity lifecycle's real stages. Replaces three previously
disconnected sources of truth: the Anchor's own prose lifecycle line,
each agent's own scattered precondition check, and Claude's own by-hand
SQL reconstruction of "what's next," redone from scratch every time it
was asked.

Read-only, deterministic, no AI - this module only classifies where an
opportunity currently stands and what the next action is; it does not
run anything and does not change anything. Every agent's own existing
precondition check stays exactly as it is (defense in depth, PB-038) -
this is a second, centralized layer, not a replacement.

Deliberately describes today's actually-enforced behavior, not the
target design ahead of it being built. Update this module in step with
whatever it is describing, never ahead of it - otherwise this becomes
exactly the kind of silent drift between the Anchor and the code it
exists to catch.
"""

from dataclasses import dataclass
from typing import Callable

from onbuild.db import evidence_ops


@dataclass
class OpportunitySnapshot:
    opportunity_id: int
    title: str
    organisation: str | None
    lifecycle_status: str | None
    lifecycle_note: str | None
    selected_at: str | None
    auto_captured_at: str | None
    evaluation_id: int | None
    evaluation_decision: str | None
    brief_id: int | None
    brief_decision: str | None
    application_id: int | None
    application_decision: str | None
    application_submitted: bool
    outcome_decision: str | None       # None if no outcome recorded yet for the latest application
    outcome_category: str | None


@dataclass
class Stage:
    key: str
    description: str
    agent: str | None                                  # module path of the agent that acts here, if any
    gate: str | None                                    # module path of the human gate that decides here, if any
    matches: Callable[[OpportunitySnapshot], bool]
    next_action: Callable[[OpportunitySnapshot], str]


def _fetch_snapshot(opportunity_id: int) -> OpportunitySnapshot:
    opportunity = evidence_ops.fetch_opportunity(opportunity_id)
    if opportunity is None:
        raise ValueError(f"No opportunity #{opportunity_id}")
    opp_id, title, organisation, _raw_text, _source, _track = opportunity

    all_opps = {row[0]: row for row in evidence_ops.fetch_all_opportunities()}
    _id, _title, _org, lifecycle_status, lifecycle_note, selected_at, auto_captured_at = all_opps[opportunity_id]

    evaluation = evidence_ops.fetch_latest_evaluation_decision(opportunity_id)
    evaluation_id, evaluation_decision = evaluation if evaluation else (None, None)

    brief = evidence_ops.fetch_latest_brief_decision(opportunity_id)
    brief_id, brief_decision = brief if brief else (None, None)

    application = evidence_ops.fetch_latest_application_decision(opportunity_id)
    if application:
        application_id, application_decision, submitted_at = application
    else:
        application_id, application_decision, submitted_at = None, None, None

    outcome_decision = outcome_category = None
    if application_id is not None:
        outcome = evidence_ops.fetch_latest_outcome_decision(application_id)
        if outcome:
            _outcome_id, outcome_decision, outcome_category = outcome

    return OpportunitySnapshot(
        opportunity_id=opp_id,
        title=title,
        organisation=organisation,
        lifecycle_status=lifecycle_status,
        lifecycle_note=lifecycle_note,
        selected_at=selected_at,
        auto_captured_at=auto_captured_at,
        evaluation_id=evaluation_id,
        evaluation_decision=evaluation_decision,
        brief_id=brief_id,
        brief_decision=brief_decision,
        application_id=application_id,
        application_decision=application_decision,
        application_submitted=submitted_at is not None,
        outcome_decision=outcome_decision,
        outcome_category=outcome_category,
    )


# Ordered - first match wins. Post-submission lifecycle_status values are
# checked first, since they represent a real gate's or agent's own
# authoritative decision and take priority over the raw artifact ladder
# below them; the artifact ladder (application/brief/evaluation) is then
# checked most-recent-artifact-first, so an opportunity with a brief AND
# an evaluation classifies by the brief's own state, not by falling back
# to "evaluated".
STAGES: list[Stage] = [
    Stage(
        key="rejected",
        description="Rejected by the recipient after submission.",
        agent=None,
        gate=None,
        matches=lambda s: s.lifecycle_status == "rejected",
        next_action=lambda s: "None - terminal. Relist only if the same posting genuinely reopens.",
    ),
    Stage(
        key="interview",
        description="Accepted for interview.",
        agent="onbuild.agents.interview_prep",
        gate="onbuild.interview_prep_decision",
        matches=lambda s: s.lifecycle_status == "interview",
        next_action=lambda s: "Run onbuild.agents.interview_prep, then onbuild.interview_prep_decision, if not already done.",
    ),
    Stage(
        key="awaiting_action",
        description="Recipient asked for something further - a response, more information.",
        agent=None,
        gate=None,
        matches=lambda s: s.lifecycle_status == "awaiting_action",
        next_action=lambda s: "Manual - fulfill whatever the message actually asked for.",
    ),
    Stage(
        key="auto_captured_pending_confirmation",
        description="Auto-captured from a receipt message - not yet confirmed as real.",
        agent=None,
        gate="onbuild.confirm_captured_applications",
        matches=lambda s: s.lifecycle_status == "submitted_pending_outcome" and s.auto_captured_at is not None,
        next_action=lambda s: "Run onbuild.confirm_captured_applications to confirm or reject this.",
    ),
    Stage(
        key="submitted_pending_outcome",
        description="Submitted, waiting on the recipient.",
        agent="onbuild.agents.outcome",
        gate="onbuild.outcome_decision",
        matches=lambda s: s.lifecycle_status == "submitted_pending_outcome",
        next_action=lambda s: (
            "Run onbuild.outcome_decision - a classified message is awaiting review."
            if s.outcome_decision is None and s.outcome_category is not None
            else "Waiting - no message classified yet. Run onbuild.mailbox or onbuild.agents.outcome once one arrives."
        ),
    ),
    Stage(
        key="closed",
        description="Deadline passed with nothing submitted - treated as dead.",
        agent=None,
        gate="onbuild.relist_opportunity",
        matches=lambda s: s.lifecycle_status == "closed",
        next_action=lambda s: "None, unless the posting genuinely reappears with a new deadline - onbuild.relist_opportunity.",
    ),
    Stage(
        key="on_hold",
        description="Genuinely still live, not currently actionable for an external reason.",
        agent=None,
        gate="onbuild.relist_opportunity",
        matches=lambda s: s.lifecycle_status == "on_hold",
        next_action=lambda s: f"On hold: {s.lifecycle_note or '(no note recorded)'} Run onbuild.relist_opportunity once a real deadline is known.",
    ),
    Stage(
        key="draft_dropped",
        description="Draft was dropped - opportunity not being pursued further.",
        agent=None,
        gate=None,
        matches=lambda s: s.application_id is not None and s.application_decision == "drop",
        next_action=lambda s: "None - terminal.",
    ),
    Stage(
        key="draft_pending_decision",
        description="Draft written, awaiting approve/revise/pause/drop.",
        agent="onbuild.agents.drafting",
        gate="onbuild.application_decision",
        matches=lambda s: s.application_id is not None and s.application_decision is None,
        next_action=lambda s: "Run onbuild.application_decision.",
    ),
    Stage(
        key="draft_paused",
        description="Draft is good, deliberately not decided yet.",
        agent=None,
        gate="onbuild.application_decision",
        matches=lambda s: s.application_id is not None and s.application_decision == "pause",
        next_action=lambda s: "Paused - will resurface in onbuild.application_decision; act sooner if the deadline warrants it.",
    ),
    Stage(
        key="draft_revise_requested",
        description="Draft sent back for revision.",
        agent="onbuild.agents.drafting",
        gate="onbuild.application_decision",
        matches=lambda s: s.application_id is not None and s.application_decision == "revise",
        next_action=lambda s: f"Run onbuild.agents.drafting {s.opportunity_id} to redraft against the revision note.",
    ),
    Stage(
        key="draft_approved_not_submitted",
        description="Draft approved, not yet confirmed submitted.",
        agent=None,
        gate="onbuild.submission_confirmation",
        matches=lambda s: (
            s.application_id is not None
            and s.application_decision == "approve"
            and not s.application_submitted
        ),
        next_action=lambda s: "Run onbuild.submission_confirmation once actually sent.",
    ),
    Stage(
        key="brief_dropped",
        description="Brief was dropped - opportunity not being pursued further.",
        agent=None,
        gate=None,
        matches=lambda s: s.brief_id is not None and s.application_id is None and s.brief_decision == "drop",
        next_action=lambda s: "None - terminal.",
    ),
    Stage(
        key="brief_pending_decision",
        description="Brief written, awaiting continue/revise/pause/drop.",
        agent="onbuild.agents.brief",
        gate="onbuild.brief_decision",
        matches=lambda s: s.brief_id is not None and s.application_id is None and s.brief_decision is None,
        next_action=lambda s: "Run onbuild.brief_decision.",
    ),
    Stage(
        key="brief_paused",
        description="Brief is good, deliberately not decided yet.",
        agent=None,
        gate="onbuild.brief_decision",
        matches=lambda s: s.brief_id is not None and s.application_id is None and s.brief_decision == "pause",
        next_action=lambda s: "Paused - will resurface in onbuild.brief_decision; act sooner if the deadline warrants it.",
    ),
    Stage(
        key="brief_revise_requested",
        description="Brief sent back for revision.",
        agent="onbuild.agents.brief",
        gate="onbuild.brief_decision",
        matches=lambda s: s.brief_id is not None and s.application_id is None and s.brief_decision == "revise",
        next_action=lambda s: f"Run onbuild.agents.brief {s.opportunity_id} to rewrite against the revision note.",
    ),
    Stage(
        key="brief_approved_not_drafted",
        description="Brief continued, not yet drafted.",
        agent="onbuild.agents.drafting",
        gate=None,
        matches=lambda s: s.brief_id is not None and s.application_id is None and s.brief_decision == "continue",
        next_action=lambda s: f"Run onbuild.agents.drafting {s.opportunity_id}.",
    ),
    Stage(
        key="evaluation_rejected",
        description="Rejected at admission - not pursued.",
        agent=None,
        gate=None,
        matches=lambda s: s.brief_id is None and s.evaluation_id is not None and s.evaluation_decision == "reject",
        next_action=lambda s: "None - terminal.",
    ),
    Stage(
        key="evaluated_pending_decision",
        description="Evaluated, awaiting admit/reject.",
        agent="onbuild.agents.evaluation",
        gate="onbuild.admission",
        matches=lambda s: s.brief_id is None and s.evaluation_id is not None and s.evaluation_decision is None,
        next_action=lambda s: "Run onbuild.admission.",
    ),
    Stage(
        key="selected_not_briefed",
        description="Chosen to actively pursue; not yet briefed.",
        agent="onbuild.agents.brief",
        gate=None,
        matches=lambda s: (
            s.brief_id is None
            and s.evaluation_id is not None
            and s.evaluation_decision == "admit"
            and s.selected_at is not None
        ),
        next_action=lambda s: f"Run onbuild.agents.brief {s.opportunity_id}.",
    ),
    Stage(
        key="admitted_unselected",
        description="Admitted, ranked, not yet chosen to actively pursue.",
        agent=None,
        gate="onbuild.selection",
        matches=lambda s: s.brief_id is None and s.evaluation_id is not None and s.evaluation_decision == "admit",
        next_action=lambda s: "See onbuild.overview for ranking. Run onbuild.selection to choose it.",
    ),
    Stage(
        key="candidate",
        description="No evaluation yet.",
        agent="onbuild.agents.evaluation",
        gate=None,
        matches=lambda s: s.evaluation_id is None,
        next_action=lambda s: "Run onbuild.agents.evaluation.",
    ),
]


def classify(opportunity_id: int) -> tuple[Stage, OpportunitySnapshot]:
    snapshot = _fetch_snapshot(opportunity_id)
    for stage in STAGES:
        if stage.matches(snapshot):
            return stage, snapshot
    raise ValueError(
        f"Opportunity #{opportunity_id} matched no known stage - registry gap, "
        f"not a data problem. Snapshot: {snapshot}"
    )


def classify_all() -> list[tuple[Stage, OpportunitySnapshot]]:
    return [classify(row[0]) for row in evidence_ops.fetch_all_opportunities()]
