"""
Database schema for the saddle's first slice: the evidence graph and the
identity/intent core. See PRODUCT_BUILD_ANCHOR.md in ON-Product-Build-Discovery
for the design reasoning behind every choice here.

Deliberately excluded from this first slice (see PB-001 and the slice
definition): track positioning, discovery-capture, and anything pipeline-related
(opportunities, applications, decisions). Those get added when the agents that
actually use them exist.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "onbuild.db"

# Node types and edge types are intentionally plain text, not a fixed SQL enum.
# PB-006: the type vocabulary is meant to be open and growable without a schema
# migration every time a new kind of evidence shows up. Validity is enforced in
# application code later, not by the database.

SCHEMA = """
-- `status`: proposed / provisional / approved / rejected. 'provisional'
-- (PB-026) is byproduct evidence a downstream agent (evaluation, brief,
-- drafting) surfaced while doing its actual job, promoted automatically
-- once its parent artifact (an evaluation, brief, or application row -
-- named by `origin_artifact_type`/`origin_opportunity_id`) is itself
-- accepted by Ingolv - inheriting the trust that decision already implies,
-- rather than requiring a second, separate explicit click. It is live
-- evidence immediately (globally, not scoped to the originating
-- opportunity - Ingolv's own call: "what the update is a byproduct of will
-- have been explicitly approved... that is security built into it").
-- `onbuild.digest` is the batch override window: everything 'provisional'
-- is listed together, anything not struck there becomes 'approved' by
-- default. Primary intake (curator-proposed, no parent artifact) never
-- gets this status - `origin_artifact_type`/`origin_opportunity_id` stay
-- NULL for it, and it stays on `onbuild.review`'s explicit per-item gate.
CREATE TABLE IF NOT EXISTS evidence_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type TEXT NOT NULL,            -- e.g. 'claim', 'portfolio_artifact', 'gap'
    quality_tag TEXT NOT NULL,          -- direct / transferable / developing / hypothetical / absent_unknown
    description TEXT NOT NULL,
    attributes TEXT,                    -- JSON payload, fields specific to node_type
    status TEXT NOT NULL DEFAULT 'proposed',
    source TEXT,                        -- where this came from, e.g. 'curator: cv_parse 2026-09-11'
    origin_artifact_type TEXT,          -- NULL (primary intake) | 'evaluation' | 'brief' | 'application' (PB-026)
    origin_opportunity_id INTEGER REFERENCES opportunities(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id INTEGER NOT NULL REFERENCES evidence_nodes(id),
    target_node_id INTEGER NOT NULL REFERENCES evidence_nodes(id),
    edge_type TEXT NOT NULL,            -- e.g. 'demonstrates', 'supports', 'surfaced_by', 'closed_by'
    quality_tag TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    source TEXT,
    origin_artifact_type TEXT,          -- see evidence_nodes above (PB-026)
    origin_opportunity_id INTEGER REFERENCES opportunities(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Identity & intent core: key/value rather than fixed columns, for the same
-- reason node types are open. History is preserved by never updating a row in
-- place: revising a fact inserts a new row and marks the old one not current,
-- so nothing is silently overwritten (PB-004).
CREATE TABLE IF NOT EXISTS identity_core (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,                  -- e.g. 'salary_floor', 'geography', 'good_fit_definition'
    value TEXT NOT NULL,                -- plain text or JSON, depending on the fact
    is_current INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'proposed',
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One opportunity being considered. `track` names which track positioning
-- this is evaluated against (PB-004's saddle component 4, "Primary/BigTech/
-- PhD" style) as plain text for now - track positioning does not yet exist
-- as its own versioned table, so this is a known simplification (PB-009's
-- evaluation agent design names a track's positioning profile as a real
-- input; until that table exists, the track is just recorded alongside the
-- opportunity rather than looked up from persisted state).
-- `lifecycle_status` (PB-029) is the first real state field for where an
-- opportunity actually sits - NULL while still active/tracked and not yet
-- submitted, then either 'closed' (PB-032 - its deadline passed with
-- nothing sent, set automatically and deterministically by
-- `evidence_ops.close_expired_opportunities()`, reversible by
-- `onbuild.relist_opportunity` if the posting reappears) or, once Ingolv
-- confirms a draft was actually sent (`onbuild.submission_confirmation`, a
-- fact-only gate: did I actually click submit, not a content judgment),
-- one of 'submitted_pending_outcome' | 'awaiting_action' | 'rejected' |
-- 'interview' - set only by that gate and by `onbuild.outcome_decision`,
-- never by the outcome agent directly (PB-022/PB-027: "never mutates
-- pipeline state directly"). Earlier lifecycle states (candidate/
-- evaluated/admitted/selected/brief-approved/drafted) still aren't
-- modeled here - named gap, PB-022 - this only covers the states the
-- outcome agent and deadline tracking actually need to update.
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    organisation TEXT,
    raw_text TEXT NOT NULL,             -- the posting text, as captured
    source TEXT,                        -- where/how this was captured
    track TEXT,
    lifecycle_status TEXT,
    application_deadline TEXT,          -- ISO date (PB-032); NULL if not stated or not yet captured
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The evaluation agent's output for one opportunity (PB-009). Written
-- directly, not through propose/approve - an evaluation is a recommendation,
-- not evidence about the person, so there is nothing to "approve" about the
-- text itself. The actual human gate for this stage is `human_decision`,
-- recorded separately by Ingolv after reading the evaluation (PB-002): the
-- gap between `suggested_action` and `human_decision` across many
-- evaluations is the agreement-rate calibration data PB-002 was designed to
-- build toward eventual admission automation.
--
-- `fit_score` and `fit_tier` (PB-019) are additions, not a replacement for
-- keeping fields separate (PB-009, against ON's own DL-003 lesson): they
-- exist only so admitted opportunities can be sorted and skimmed on a
-- pending-application list, not to collapse or stand in for the reasoning
-- in the other fields. `fit_tier` in particular makes a genuine grey-zone
-- case - real substantive fit alongside serious gaps - visible as its own
-- category rather than forcing a binary admit/reject read.
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    fit_summary TEXT NOT NULL,
    distinctiveness TEXT NOT NULL,
    gates_summary TEXT NOT NULL,
    countercase TEXT NOT NULL,          -- required even when fit looks strong (PB-009)
    requirement_matches TEXT NOT NULL,  -- JSON: [{requirement, match_quality, rationale, evidence_node_ids}]
    gaps_summary TEXT,
    fit_score INTEGER,                  -- 1-10, ranking aid only - not the verdict (PB-019)
    fit_tier TEXT,                      -- 'strong_match' | 'stretch' | 'mismatch' (PB-019)
    suggested_action TEXT NOT NULL,     -- 'admit' | 'reject' | 'flag' (PB-009)
    human_decision TEXT,                -- Ingolv's actual decision, filled in later
    decided_at TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Persistent company knowledge (PB-023). Researched once, reused across
-- every opportunity at the same company from then on - the point Ingolv
-- named directly: "in time, the system will build knowledge of companies
-- and relevant fields, that will help in making assessments for
-- unsolicited opportunities." Written directly by the brief-writing agent,
-- not through propose/approve (Ingolv's own call): getting a company's
-- public positioning wrong is lower-stakes than misrepresenting Ingolv's
-- own evidence, and it is self-correcting - the agent re-researches and
-- updates it rather than needing a human review cycle first.
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    general_profile TEXT,               -- researched independently of any one opportunity
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A company's positioning within one field (e.g. "AI transformation",
-- "biomedical engineering") - also researched independently of any one
-- opportunity, so the read isn't anchored to what a specific posting
-- claims about itself. `quality_tag` distinguishes direct evidence (e.g. a
-- published AI strategy) from a reasoned inference when no direct evidence
-- exists - Ingolv: "if there is no direct evidence this can also be
-- inferred." One company can have positions in more than one field.
CREATE TABLE IF NOT EXISTS company_field_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    field TEXT NOT NULL,
    positioning TEXT NOT NULL,
    quality_tag TEXT NOT NULL,          -- 'direct' | 'inferred'
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A company's presence at one location (e.g. "Copenhagen, Denmark") - the
-- same reasoning as field positions, for a different dimension: a
-- multinational's footprint varies by location as much as by field
-- (Ingolv: "IBM in Denmark is not necessarily the same as in Mongolia or
-- the US"). Also researched independently of any one opportunity. One
-- company can have presence rows at more than one location.
CREATE TABLE IF NOT EXISTS company_location_presence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    location TEXT NOT NULL,
    presence TEXT NOT NULL,
    quality_tag TEXT NOT NULL,          -- 'direct' | 'inferred'
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The brief-writing agent's output for one opportunity (PB-023). Written
-- directly, like evaluations - a brief is a strategic document, not
-- evidence about Ingolv. `company_profile`/`location_presence`/
-- `field_positioning` are stored as snapshots of what was actually used
-- when the brief was written, even though the underlying `companies`/
-- `company_location_presence`/`company_field_positions` rows may be
-- refreshed later - the brief should always show the reasoning that led to
-- it, not silently inherit a later update. The `_id` columns keep the
-- traceable link to that source. `human_decision` is the three-way brief
-- gate Ingolv specified (PB-022) - not binary approve/reject.
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    company_id INTEGER REFERENCES companies(id),
    company_location_presence_id INTEGER REFERENCES company_location_presence(id),
    company_field_position_id INTEGER REFERENCES company_field_positions(id),
    company_profile TEXT NOT NULL,
    location_presence TEXT,
    field_positioning TEXT NOT NULL,
    position_fit TEXT NOT NULL,
    candidacy_fit_summary TEXT NOT NULL,
    strategic_approach TEXT NOT NULL,
    human_decision TEXT,                -- 'continue' | 'revise' | 'pause' | 'drop' (PB-036)
    revision_notes TEXT,
    decided_at TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The drafting agent's output for one opportunity (PB-025). Only ever runs
-- against a brief whose human_decision is 'continue' - application code
-- enforces this, not the schema. Written directly, like evaluations and
-- briefs - a draft is generated content, not evidence about Ingolv.
--
-- `captured_application_text` holds whatever was manually captured from the
-- real application (a printed-to-PDF page, pasted text) - real application
-- portals routinely ask for things a job posting never mentions (a
-- "message to the hiring team" instead of a cover letter, per-role
-- description boxes, specific questions), and reviewing that real page is a
-- genuine judgment moment for Ingolv, not something to automate away
-- (PB-025). Nullable - a draft can still be produced from the posting and
-- brief alone when nothing was captured, with `application_format_assessment`
-- saying plainly what's unknown as a result. This same input slot is where
-- a future automated "probe the application" capability would plug in
-- later, without changing anything else - PB-025's "build room for it, gate
-- its activation" boundary.
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    captured_application_text TEXT,
    application_format_assessment TEXT NOT NULL,
    tailored_cv TEXT NOT NULL,
    cover_letter_or_message TEXT NOT NULL,
    application_form_data TEXT NOT NULL,
    question_responses TEXT,
    portfolio_recommendation TEXT NOT NULL,
    human_decision TEXT,                -- 'approve' | 'revise' | 'pause' | 'drop' (PB-036)
    revision_notes TEXT,
    decided_at TEXT,
    source TEXT,
    submitted_at TEXT,                  -- set by onbuild.submission_confirmation (PB-029)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The outcome agent's output for one captured message about a submitted
-- application (PB-029). Written directly, like evaluations/briefs/drafts -
-- a classification, not evidence about Ingolv; the real human gate is the
-- separate decision recorded afterward by `onbuild.outcome_decision`.
-- `captured_message_text` is required, not nullable like drafting's
-- captured-application slot - this agent has nothing to classify without a
-- real message, unlike drafting, which can still produce a generic CV/
-- cover letter from the posting and brief alone. Mailbox-access mechanism
-- deliberately not built (PB-027's boundary, same reasoning as PB-025's
-- deferred application-portal browser automation): a message is captured
-- and handed to this agent as a plain text file, not read live from a
-- real inbox.
-- Live-mailbox watermark (PB-030) - a singleton row tracking the highest
-- IMAP UID already processed, so `onbuild.mailbox` never reprocesses a
-- message. Not IMAP's own Seen flag: the mailbox is opened strictly
-- read-only and fetched via BODY.PEEK, so nothing is ever marked read on
-- the live account itself - the database is the only place progress is
-- tracked, per the Toolkit invariant (PB-007) that the database is the
-- sole source of truth.
CREATE TABLE IF NOT EXISTS mailbox_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_processed_uid INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A live-fetched message that onbuild.mailbox could not confidently match
-- to exactly one opportunity awaiting outcome (zero or multiple candidate
-- matches) - held here rather than silently dropped or guessed at (PB-004's
-- "nothing captured is ever silently dropped" discipline, applied to live
-- mail the same way it already applies to discovery capture). Resolved
-- manually for now: reviewed directly, then re-run through
-- `onbuild.agents.outcome` by hand once the right opportunity is known.
CREATE TABLE IF NOT EXISTS unmatched_mailbox_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    sender TEXT,
    subject TEXT,
    message_text TEXT NOT NULL,
    candidate_opportunity_ids TEXT,     -- JSON list - empty if zero candidates, >1 if ambiguous
    status TEXT NOT NULL DEFAULT 'unresolved',  -- 'unresolved' | 'resolved'
    received_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The interview-preparation agent's output for one opportunity (PB-035,
-- resolving PB-027's placement). Written directly, like evaluations/
-- briefs/drafts/outcomes - a strategy document, not evidence about
-- Ingolv. Pulls the brief as its starting point (not re-derived from
-- scratch, same discipline PB-023 established for brief-on-evaluation)
-- plus whatever the actual submitted application said, plus the real
-- interview-invitation message for who the interview is actually
-- scheduled with. `interviewer_research` and `office_leadership_research`
-- are kept separate because they answer different questions - who
-- Ingolv will actually be speaking with, versus who runs the office the
-- role sits in, which may or may not be the same person.
CREATE TABLE IF NOT EXISTS interview_preps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    outcome_id INTEGER REFERENCES outcomes(id),
    interviewer_research TEXT NOT NULL,
    office_leadership_research TEXT NOT NULL,
    talking_points TEXT NOT NULL,
    requirement_coverage TEXT NOT NULL,  -- JSON: [{requirement, coverage: 'direct'|'indirect'|'none', evidence_node_ids, note}]
    human_decision TEXT,                -- 'approve' | 'revise' | 'pause' | 'drop' (PB-036)
    revision_notes TEXT,
    decided_at TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    captured_message_text TEXT NOT NULL,
    submission_confirmed INTEGER,       -- 1/0/NULL - does this message itself confirm receipt
    category TEXT NOT NULL,             -- 'receipt_confirmation' | 'interview' | 'rejection' | 'further_info' | 'other_request' | 'unclear'
    rationale TEXT NOT NULL,
    suggested_next_step TEXT,
    human_decision TEXT,                -- 'confirm' | 'recategorize' | 'ignore'
    decided_category TEXT,              -- filled only when human_decision='recategorize'
    revision_notes TEXT,
    decided_at TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Columns added to an existing table after it was first created elsewhere.
# CREATE TABLE IF NOT EXISTS won't add these to a database that already has
# the table (e.g. `evaluations` before PB-019), so init_db adds any missing
# ones explicitly. Append here, never remove - the same append-only
# discipline as everything else in this schema.
_COLUMN_MIGRATIONS = {
    "evaluations": [
        ("fit_score", "INTEGER"),
        ("fit_tier", "TEXT"),
    ],
    "briefs": [
        ("company_location_presence_id", "INTEGER"),
        ("location_presence", "TEXT"),
    ],
    "evidence_nodes": [
        ("origin_artifact_type", "TEXT"),
        ("origin_opportunity_id", "INTEGER REFERENCES opportunities(id)"),
    ],
    "evidence_edges": [
        ("origin_artifact_type", "TEXT"),
        ("origin_opportunity_id", "INTEGER REFERENCES opportunities(id)"),
    ],
    "opportunities": [
        ("lifecycle_status", "TEXT"),
        ("application_deadline", "TEXT"),
    ],
    "applications": [
        ("submitted_at", "TEXT"),
    ],
}


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the schema if it doesn't already exist. Safe to call repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for table, columns in _COLUMN_MIGRATIONS.items():
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for column, col_type in columns:
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                    )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
