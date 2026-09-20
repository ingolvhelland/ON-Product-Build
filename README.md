# ON Product Build

The actual codebase for Opportunity Navigation's rebuild. The governing design
record — why every decision here was made, in what order, and what it does
not yet resolve — lives in `PRODUCT_BUILD_ANCHOR.md` and `PRODUCT_BUILD_LOG.md`,
in the separate `ON-Product-Build-Discovery` folder. This repository is where
that design actually gets built; it is not where decisions get made.

## Current state

Four of the pipeline's agents are built and have run against real
opportunities: curator (evidence intake), evaluation (fit assessment),
brief-writing (company/field/location research and strategy), and drafting
(application material). Every human decision gate - review, admission,
brief, application - is a plain command-line tool with no AI in it. See
`PRODUCT_BUILD_LOG.md` entries PB-009 through PB-025 for the reasoning
behind this scope and how each stage was judged.

Known gaps, not yet acted on: the evidence graph is incomplete relative to
Ingolv's full background (more source material still needs to go through
the curator, deliberately, as real evaluation use reveals it's needed);
there is no track positioning as a persisted, versioned table (PB-008/
PB-019) - track is a plain string for now; no ranked-list view or
selection lifecycle state (PB-022); no email/outcome agent; no
application-portal browser automation (PB-025 - real application content
is manually captured for now, deliberately, given the safety stakes of an
agent that could click things on a live application system); no learning
loop yet feeds later-discovered application-stage information back into
evaluation calibration.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
python -m onbuild.db.schema   # creates data/onbuild.db
```

## Usage

```bash
python -m onbuild.agents.curator path/to/some.txt   # propose evidence from raw text
python -m onbuild.review                            # approve/reject what was proposed
python -m onbuild.agents.baseline_cv --out cv.txt    # generate the baseline CV from approved evidence
python -m onbuild.agents.evaluation path/to/posting.txt \
    --title "..." --organisation "..." --track "Primary"  # evaluate one opportunity
python -m onbuild.admission                          # record admit/reject on evaluated opportunities
python -m onbuild.agents.brief <opportunity_id>       # write a strategic brief for an admitted opportunity
python -m onbuild.brief_decision                     # record continue/revise/drop on a brief
python -m onbuild.agents.drafting <opportunity_id> \
    [--captured-application path/to/captured.txt]    # draft application material for a continued brief
python -m onbuild.application_decision              # record approve/revise/drop on a draft
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

`drafting`'s `--captured-application` flag is optional: if you've manually
captured what the real application page actually asks for (a PDF print, a
pasted screen), pass it as a plain text file and the draft is shaped to
match; without it, drafting says plainly what it doesn't know rather than
guessing at the real form's shape.

`digest` is a different kind of gate (PB-026): evidence that evaluation,
brief-writing, or drafting proposed as a byproduct of their actual work
(not the curator's primary intake, which always stays on `review`)
goes live immediately, globally, the moment you accept the artifact it rode
in on - admitting an evaluation, continuing a brief, approving a draft.
Run `digest` whenever you want to check what that's let through;
everything listed becomes approved unless you strike it in that same run.
