# ON Product Build

The actual codebase for Opportunity Navigation's rebuild. The governing design
record — why every decision here was made, in what order, and what it does
not yet resolve — lives in `PRODUCT_BUILD_ANCHOR.md` and `PRODUCT_BUILD_LOG.md`,
in the separate `ON-Product-Build-Discovery` folder. This repository is where
that design actually gets built; it is not where decisions get made.

## Current state

Six of the pipeline's agents are built: curator (evidence intake),
evaluation (fit assessment), brief-writing (company/field/location
research and strategy), drafting (application material), outcome
(classifying a post-submission message, fed either by a live, read-only
scan of the dedicated job-search mailbox or a manually captured file,
and - since PB-040 - applying that classification directly rather than
proposing it), and interview-prep (researching who the interview is
actually with, 5-7 evidence-grounded talking points, and
requirement-by-requirement coverage) - all but interview-prep have run
against real opportunities; interview-prep is verified but not yet
exercised for real, since no real opportunity has reached the interview
stage yet. Every human decision gate before submission - review,
admission, selection, brief, application, submission confirmation - is a
plain command-line tool with no AI in it; after submission, outcome and
interview-prep are agents whose output is either applied directly
(outcome, PB-040) or still gated (interview-prep). See
`PRODUCT_BUILD_LOG.md` entries PB-009 through PB-040 for the reasoning
behind this scope and how each stage was judged.

No interface exists yet, deliberately (see Log PB-034): a web dashboard
was built and then removed the same day, on the judgment that interface
design should wait until the underlying system is finished and be derived
from where a human actually needs to touch it and what it needs to feed
back, not adopted as a generic pattern mid-build. `onbuild.overview` is
the plain-text version of the same ranked-list view, in the same CLI
style as everything else here.

The pipeline's own structure - which agent or gate is next for a given
opportunity - is now named as an explicit, first-class part of the
system (PB-038), not left implicit in each agent's own precondition
check. `onbuild.pipeline` is a read-only stage registry classifying
every opportunity into exactly one of ~20 mutually-exclusive stages;
`onbuild.pipeline_status` is the plain-text tool built on top of it.
Orchestration stays deliberately surface-only for now (no auto-run) -
this reports where things stand, it doesn't move anything - and every
agent's own existing precondition check stays exactly as it was
(defense in depth, not replaced). PB-038 also settled, and PB-040
implemented, the *authorship principle* governing every post-submission
state change: before submission the user authors every change
(human-gated, as already built); after submission the recipient (the
employer) authors it, so `onbuild.agents.outcome` now applies rejection/
interview/further_info/other_request/receipt_confirmation directly to
`lifecycle_status` the moment it classifies a message, notifying rather
than asking - `unclear` is the sole exception, since no real fact yet
exists to transcribe. `onbuild.outcome_decision` is no longer a per-
message gate; it's two separate, much rarer tools now: resolving what
the agent genuinely couldn't apply (`unclear`, or anything predating
PB-040), and `--override <opportunity_id>`, an explicit correction path
for when the agent's read was wrong.

Admission and pursuit are now two separate decisions (PB-039): admission
(`onbuild.admission`) only says an opportunity is not a bad fit; a new
gate, `onbuild.selection`, records the later, separate choice to actually
invest effort in one right now, setting `opportunities.selected_at`.
`onbuild.agents.brief` requires both - admitted and selected - and now
actually checks this at runtime before calling the agent, rather than
only claiming to in its own docstring. `onbuild.overview` is unaffected
(it still shows every admitted opportunity, selected or not - selection
narrows what brief-writing will run against, not what's visible).

Known gaps, not yet acted on: the evidence graph is incomplete relative to
Ingolv's full background (more source material still needs to go through
the curator, deliberately, as real evaluation use reveals it's needed);
there is no track positioning as a persisted, versioned table (PB-008/
PB-019) - track is a plain string for now; the two pending Accura and
Fælles Digital outcome records that predate PB-040 are still sitting
unresolved, deliberately - resolving them means actually reading those
emails, Ingolv's own call, via `onbuild.outcome_decision`; no scanning
agent yet (PB-038 - deliberately built last, once there's a
real registry and selection state for it to feed into); no interface of
any kind (PB-034); no application-portal browser automation (PB-025 -
deliberately not extended to live application systems the way mailbox
access was, given the different safety stakes of an agent that could
click something on a live application system); no recurring schedule for
the live mailbox scan (PB-030 - it runs a single pass on demand today;
unattended scheduling is a standing-configuration choice not yet made);
no learning loop yet feeds later-discovered application-stage information
back into evaluation calibration.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY (and GMAIL_ADDRESS/GMAIL_APP_PASSWORD if using onbuild.mailbox)
python -m onbuild.db.schema   # creates data/onbuild.db
```

## Usage

```bash
python -m onbuild.agents.curator path/to/some.txt   # propose evidence from raw text
python -m onbuild.review                            # approve/reject what was proposed
python -m onbuild.agents.baseline_cv --out cv.txt    # generate the baseline CV from approved evidence
python -m onbuild.agents.evaluation path/to/posting.txt \
    --title "..." --organisation "..." --track "Primary" [--deadline YYYY-MM-DD]  # evaluate one opportunity
python -m onbuild.admission                          # record admit/reject on evaluated opportunities
python -m onbuild.selection                          # choose which admitted opportunities to actively pursue right now
python -m onbuild.agents.brief <opportunity_id>       # write a strategic brief for an admitted AND selected opportunity
python -m onbuild.brief_decision                     # record continue/revise/pause/drop on a brief
python -m onbuild.agents.drafting <opportunity_id> \
    [--captured-application path/to/captured.txt]    # draft application material for a continued brief
python -m onbuild.application_decision              # record approve/revise/pause/drop on a draft
python -m onbuild.submission_confirmation           # confirm an approved draft was actually sent
python -m onbuild.mailbox                           # scan the job-search inbox for new mail, match, and classify
python -m onbuild.agents.outcome <opportunity_id> path/to/message.txt  # classify one message manually (no live match found, or testing)
python -m onbuild.outcome_decision                  # resolve an 'unclear' classification or a pre-PB-040 legacy outcome
python -m onbuild.outcome_decision --override <opportunity_id>  # correct an already-applied classification
python -m onbuild.agents.interview_prep <opportunity_id>  # research and strategy for an opportunity at the interview stage
python -m onbuild.interview_prep_decision           # record approve/revise/pause/drop on an interview prep
python -m onbuild.register_external_application \    # register an application sent outside this system
    --title "..." --organisation "..." [--posting-file f.txt] [--sent-file f.txt]
python -m onbuild.resolve_unmatched <unmatched_id> <opportunity_id>  # classify an already-captured unmatched message
python -m onbuild.overview                          # ranked list of admitted, active/on-hold/awaiting-outcome opportunities
python -m onbuild.hold_opportunity <opportunity_id> "<note>"  # mark an opportunity on hold - live but not currently actionable
python -m onbuild.relist_opportunity <opportunity_id> <new_deadline>  # reopen a closed or on-hold opportunity with a new deadline
python -m onbuild.pipeline_status                   # read-only: every opportunity's current pipeline stage and next action
python -m onbuild.digest                            # batch-approve/strike byproduct evidence live since the last run
```

`review` is the only place anything moves from `status='proposed'` to
`approved`/`rejected` - there is no AI in it on purpose (PB-010, PB-012):
judgment stays human. It walks evidence nodes, then edges, then identity
facts, one at a time; `s` skips an item for later, `q` stops immediately.
Nothing is ever deleted, so re-running it is always safe.

`admission`, `brief_decision`, and `application_decision` are the same
kind of gate for opportunities, briefs, and drafts respectively (PB-002,
PB-020, PB-022, PB-025) - also no AI in any of them. Each shows only the
most recent record per opportunity and records your actual decision
alongside the agent's own suggested action; the gap between the two, over
time, is the calibration data PB-002 was designed to build toward.

`brief_decision`, `application_decision`, and `interview_prep_decision`
share a fourth option beyond approve/revise/drop: `pause` (PB-036) -
"this is genuinely good, I'm just not deciding yet." A paused item isn't
lost: it reappears the next time you run the same tool, sorted by the
opportunity's own deadline (soonest first) so a paused decision with a
real deadline coming up surfaces on its own, with the same four options
available again. `onbuild.overview` also flags which opportunities have
something paused, in the same table that already shows deadline urgency.

`drafting`'s `--captured-application` flag is optional: if you've manually
captured what the real application page actually asks for (a PDF print, a
pasted screen), pass it as a plain text file and the draft is shaped to
match; without it, drafting says plainly what it doesn't know rather than
guessing at the real form's shape.

`digest` is a different kind of gate (PB-026): evidence that evaluation,
brief-writing, drafting, or outcome proposed as a byproduct of their actual
work (not the curator's primary intake, which always stays on `review`)
goes live immediately, globally, the moment you accept the artifact it rode
in on - admitting an evaluation, continuing a brief, approving a draft,
confirming an outcome classification. Run `digest` whenever you want to
check what that's let through; everything listed becomes approved unless
you strike it in that same run.

`submission_confirmation` is a fact-only gate, not a content judgment
(PB-029): approving a draft means it's good enough to send, not that it
has been sent. Confirming it here sets `applications.submitted_at` and
moves the opportunity's `lifecycle_status` to `submitted_pending_outcome` -
required before `outcome` will run for that opportunity.

`outcome` classifies one message about an already-submitted opportunity
into one of six categories - receipt_confirmation, interview, rejection,
further_info, other_request, unclear - and applies that classification
directly for every category except `unclear` (PB-038/PB-040's authorship
principle: past submission, the recipient authored this fact, not
Ingolv, so recording it isn't a proposal). It moves `lifecycle_status`
to `interview`, `rejected`, or `awaiting_action` as appropriate, leaves
it alone for `receipt_confirmation` (already correct), and promotes any
byproduct evidence it proposed alongside the classification, all in the
same call. `unclear` is the one category left for a human: no real fact
exists yet to transcribe. `outcome_decision` (no arguments) resolves
those - and anything recorded before PB-040 existed - by picking the
real category, which is then applied the same way. `outcome_decision
--override <opportunity_id>` is the separate, explicit correction path
for when an already-applied classification turns out to have been
wrong - deliberately not part of the default flow, since override should
be the exception, not the norm.

`mailbox` (PB-030) reads the dedicated job-search Gmail account live -
`GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` in `.env` (see `.env.example` for how
to generate an App Password; never the account's real password). It is
strictly read-only by construction: the mailbox is opened in read-only
mode and every fetch uses `BODY.PEEK[]`, so nothing about the live account
ever changes, and there is no send capability anywhere in it. It matches
each new message to a candidate opportunity by organisation name; on a
single confident match it calls `outcome` directly - which, since
PB-040, may itself change that opportunity's `lifecycle_status` on the
spot - and on zero or multiple matches it holds the message in
`unmatched_mailbox_messages` rather than guessing - review those
directly and re-run `outcome` manually once the right opportunity is
clear. A mismatch here is corrected via `onbuild.outcome_decision
--override`, same as any other wrong classification. Safe to run
repeatedly - it
tracks its own progress in the database and only ever looks at mail newer
than what it already processed. Runs a single pass each time you call it;
wiring it to run on a recurring schedule is a separate choice, not made
here.

`overview` (PB-032) is the ranked admitted-opportunities list - it runs an
automatic, deterministic check first (any active opportunity whose
`--deadline` has passed with nothing submitted gets `lifecycle_status`
set to `closed`), then prints what's left, soonest deadline first, then
by `fit_score`. `hold_opportunity` (PB-037) marks an opportunity
`on_hold` instead - genuinely still live but not currently actionable for
an external reason (a portal that reappeared but isn't open yet, a
clarification requested and pending), distinct from `closed` which means
the thing is actually dead; its note stays visible in `overview` so the
reason isn't forgotten. `relist_opportunity` reopens a `closed` or
`on_hold` opportunity with a new deadline once one is actually known,
clearing the hold note.

`agents.interview_prep` (PB-035) only runs once an interview invitation
has been applied for an opportunity (`lifecycle_status='interview'`,
almost always set directly by `outcome` itself since PB-040) and its
brief is `continue`. It reads the
real interview-invitation message directly to identify who the interview
is actually scheduled with and researches them, separately researches who
runs the office the position sits in (named people, not the brief's own
company-location presence research), then produces 5-7 evidence-grounded
talking points consistent with what was actually submitted and a
requirement-by-requirement coverage list building on the evaluation's own
`requirement_matches`. The third agent with real web access, after
brief-writing. `interview_prep_decision` is the same four-way gate
(approve/revise/pause/drop) as brief and application.

`selection` (PB-039) is a fact-only gate, the same reasoning as
`submission_confirmation` and `hold_opportunity`: admission alone means
"not a bad fit," not "pursue this now" - this records the separate,
later choice to actually invest effort in an admitted opportunity,
setting `opportunities.selected_at`. Presents admitted opportunities not
yet selected in the same ranked order as `onbuild.overview`.
`onbuild.agents.brief` requires both admission and selection before it
will run, checked at the start of `write_brief()` rather than assumed.

`pipeline` (PB-038) is a read-only registry - a single, explicit,
ordered list of ~20 mutually-exclusive stages an opportunity can be in,
replacing three previously disconnected sources of truth: the Anchor's
own prose lifecycle line, each agent's own scattered precondition check,
and re-deriving "what's next" by hand from the database on request. It
classifies deterministically, with no AI in it, and it does not change
anything - every agent's own existing precondition check stays exactly
as it was (defense in depth, not a replacement). It deliberately
describes today's actually-enforced behavior, not the design ahead of
it - `selected` (PB-022) does not appear as a stage, because brief-writing
doesn't require it yet. `pipeline_status` is the plain CLI built on top
of it, distinct from `onbuild.overview`: `overview` is the admitted-
ranked list a human chooses from; `pipeline_status` is a status report
across the whole pipeline, from opportunities with no evaluation yet
through to terminal outcomes.

`register_external_application` (PB-031) is for an opportunity that was
drafted and sent entirely outside this system - it creates the
opportunity plus the minimal placeholder brief/application rows the
schema still requires, and marks it `submitted_pending_outcome`
immediately, since in real life it already is. `resolve_unmatched` closes
the loop when `mailbox` already captured a message before the opportunity
it belongs to existed: pass the unmatched message's id and the now-known
opportunity id, and it classifies that message directly without needing
`mailbox` to re-fetch anything live.
