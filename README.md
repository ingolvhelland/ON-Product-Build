# ON Product Build

The actual codebase for Opportunity Navigation's rebuild. The governing design
record — why every decision here was made, in what order, and what it does
not yet resolve — lives in `PRODUCT_BUILD_ANCHOR.md` and `PRODUCT_BUILD_LOG.md`,
in the separate `ON-Product-Build-Discovery` folder. This repository is where
that design actually gets built; it is not where decisions get made.

## Current state

First vertical slice in progress: the curator agent's round-trip test
(raw CV in → parsed into the saddle → baseline CV generated from what's
approved → compared against the original). See `PRODUCT_BUILD_LOG.md` entries
PB-009 through PB-011 for the reasoning behind this scope.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
python -m onbuild.db.schema   # creates data/onbuild.db
```
