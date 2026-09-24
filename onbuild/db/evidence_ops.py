"""
Shared read/write operations on the evidence graph and identity core.

Every agent that touches the graph (curator, evaluation, and any future
one) defines its own @tool-decorated wrapper functions so it can pass its
own `source` tag correctly - but the actual SQL lives here once, so two
agents doing the same kind of write don't drift out of sync with each
other's fixes (see PB-012/PB-014/PB-016 for the curator-side history this
logic already carries). Importing another agent's already-decorated tool
function directly would also silently misattribute provenance, since the
source tag would resolve from the *defining* module's constant, not the
caller's - this module exists specifically so that doesn't happen.
"""

import sqlite3

from onbuild.db.schema import DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def insert_evidence_node(
    node_type: str,
    quality_tag: str,
    description: str,
    attributes: str | None,
    source: str,
    origin_artifact_type: str | None = None,
    origin_opportunity_id: int | None = None,
) -> int:
    """`origin_artifact_type`/`origin_opportunity_id` (PB-026) mark this as
    byproduct evidence tied to a downstream artifact - 'evaluation' |
    'brief' | 'application' - so it can be promoted to 'provisional' once
    that artifact is accepted. Left NULL by the curator (primary intake,
    no parent artifact to inherit trust from)."""
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO evidence_nodes
                (node_type, quality_tag, description, attributes, status, source,
                 origin_artifact_type, origin_opportunity_id)
            VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)
            """,
            (
                node_type,
                quality_tag,
                description,
                attributes or None,
                source,
                origin_artifact_type,
                origin_opportunity_id,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def insert_identity_fact(key: str, value: str, source: str) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO identity_core (key, value, is_current, status, source)
            VALUES (?, ?, 0, 'proposed', ?)
            """,
            (key, value, source),
        )
        conn.commit()
    finally:
        conn.close()


def insert_evidence_edge(
    source_node_id: int,
    target_node_id: int,
    edge_type: str,
    quality_tag: str,
    source: str,
    origin_artifact_type: str | None = None,
    origin_opportunity_id: int | None = None,
) -> int:
    """See insert_evidence_node's origin_artifact_type/origin_opportunity_id
    docstring (PB-026) - same meaning here."""
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO evidence_edges
                (source_node_id, target_node_id, edge_type, quality_tag, status, source,
                 origin_artifact_type, origin_opportunity_id)
            VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)
            """,
            (
                source_node_id,
                target_node_id,
                edge_type,
                quality_tag,
                source,
                origin_artifact_type,
                origin_opportunity_id,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def insert_opportunity(
    title: str,
    organisation: str | None,
    raw_text: str,
    source: str | None,
    track: str | None,
    application_deadline: str | None = None,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO opportunities (title, organisation, raw_text, source, track, application_deadline)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, organisation, raw_text, source, track, application_deadline),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_opportunity_track(opportunity_id: int, track: str) -> None:
    """Sets track on a candidate opportunity that didn't have one yet -
    most often one the scanning agent found (PB-042 deliberately leaves
    track NULL there). Which track an opportunity is evaluated against is
    a human/evaluation-time call, never a discovery-time guess; this is
    what `onbuild.agents.evaluation --opportunity-id` persists that call
    onto, so it's visible everywhere else the opportunity is read, not
    just inside that one evaluation run."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET track=? WHERE id=?",
            (track, opportunity_id),
        )
        conn.commit()
    finally:
        conn.close()


def opportunity_exists(title: str, organisation: str | None) -> bool:
    """Dedup check for the scanning agent (PB-042): normalized
    (lowercased, trimmed) title+organisation match against every
    opportunity already in the system, regardless of how it got there -
    manual entry, a previous scan, or a mailbox-fed discovery all share
    the same front door (PB-038), so they all count as prior art here."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM opportunities "
            "WHERE lower(trim(title)) = lower(trim(?)) "
            "AND lower(trim(coalesce(organisation, ''))) = lower(trim(coalesce(?, ''))) "
            "LIMIT 1",
            (title, organisation),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_candidate_opportunity(
    title: str,
    organisation: str | None,
    raw_text: str,
    source: str,
    application_deadline: str | None = None,
) -> int | None:
    """The scanning agent's one write path (PB-042) - wraps
    `opportunity_exists`'s dedup check and `insert_opportunity` together
    so every caller (the full scan, mailbox-fed extraction) gets the same
    guarantee for free. Returns the new opportunity's id, or None if it's
    a duplicate of something already tracked (nothing is inserted in
    that case). `track` is deliberately left NULL - which track this
    belongs to is a human/evaluation-time call (`onbuild.agents.evaluation
    --track`), not something a discovery agent should assert."""
    if opportunity_exists(title, organisation):
        return None
    return insert_opportunity(
        title, organisation, raw_text, source, None, application_deadline
    )


def record_unverified_lead(
    title: str, organisation: str | None, source_url: str, note: str | None
) -> int | None:
    """A scanning-agent write path distinct from `insert_candidate_opportunity`
    (PB-052): for when only a MENTION of a role was found - a job-alert
    stub, a search snippet, "N school alumni" and a link - never the
    real posting itself. Every LinkedIn digest email in this system's
    real history turned out to be exactly this shape, and all 41 got
    recorded as full candidates and evaluated against nothing but a
    one-line stub before this existed. Flags `lead_only_at` so
    `onbuild.agents.evaluation` refuses to run against it and
    `onbuild.pipeline` surfaces it as its own distinct stage, pointing
    at `source_url` so Ingolv can find the real posting by hand if it's
    worth pursuing. Same dedup as `insert_candidate_opportunity` -
    returns None if this title+organisation is already tracked by any
    route, including as an already-verified opportunity."""
    if opportunity_exists(title, organisation):
        return None
    raw_text = (
        f"(not yet verified - only a mention was found, not the real "
        f"posting) {note or ''}\n\nFind the actual listing at: {source_url}"
    ).strip()
    opportunity_id = insert_opportunity(
        title, organisation, raw_text, source_url, None, None
    )
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET lead_only_at=datetime('now') WHERE id=?",
            (opportunity_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return opportunity_id


def fetch_lead_status(opportunity_id: int) -> str | None:
    """`lead_only_at` for one opportunity (PB-052) - `None` means it's a
    real, verified posting (or was never a lead at all); a timestamp
    means only a mention was ever found."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT lead_only_at FROM opportunities WHERE id=?", (opportunity_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def attach_verified_posting(opportunity_id: int, raw_text: str, source: str | None) -> None:
    """Upgrades a lead-only opportunity (PB-052) once the real posting
    has actually been found - overwrites `raw_text` with the genuine
    posting content and clears `lead_only_at`, so
    `onbuild.agents.evaluation` will now run against it. `source`,
    if given, replaces the lead's own link with wherever the real
    posting was actually found (may be the same URL)."""
    conn = connect()
    try:
        if source:
            conn.execute(
                "UPDATE opportunities SET raw_text=?, source=?, lead_only_at=NULL WHERE id=?",
                (raw_text, source, opportunity_id),
            )
        else:
            conn.execute(
                "UPDATE opportunities SET raw_text=?, lead_only_at=NULL WHERE id=?",
                (raw_text, opportunity_id),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_top_evaluations(limit: int = 5) -> list[tuple]:
    """The highest-`fit_score` evaluations across every opportunity,
    regardless of source (PB-042) - context for the scanning agent's own
    query generation. Deliberately not scoped to admitted-only or to
    scan-discovered opportunities: a good fit found by hand, outside the
    normal search parameters, should shape future searches exactly as
    much as one the scan agent found itself (Ingolv's own framing).
    Fewer rows than `limit` simply means less evaluation history exists
    yet - the caller is expected to lean more on the evidence graph
    directly when this returns little or nothing."""
    conn = connect()
    try:
        return conn.execute(
            """
            SELECT o.title, o.organisation, e.fit_summary, e.distinctiveness,
                   e.requirement_matches, e.fit_score, e.fit_tier
            FROM evaluations e
            JOIN opportunities o ON o.id = e.opportunity_id
            WHERE e.fit_score IS NOT NULL
            ORDER BY e.fit_score DESC, e.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def insert_scan_source(label: str, url: str) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO scan_sources (label, url) VALUES (?, ?)",
            (label, url),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_scan_sources(active_only: bool = False) -> list[tuple]:
    conn = connect()
    try:
        query = "SELECT id, label, url, active FROM scan_sources"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY id"
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def set_scan_source_active(source_id: int, active: bool) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE scan_sources SET active=? WHERE id=?",
            (int(active), source_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_expired_opportunities() -> list[int]:
    """Deterministic, not a judgment - a deadline having passed with
    nothing submitted is a plain fact, so this runs automatically as a
    side effect of `onbuild.overview` rather than needing its own gate
    (PB-032). Only ever touches opportunities still `lifecycle_status IS
    NULL` (active, not yet submitted, not already closed) - never
    overrides a real outcome or an in-progress submission."""
    conn = connect()
    try:
        ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM opportunities WHERE lifecycle_status IS NULL "
                "AND application_deadline IS NOT NULL "
                "AND application_deadline < date('now')"
            ).fetchall()
        ]
        if ids:
            conn.executemany(
                "UPDATE opportunities SET lifecycle_status='closed' WHERE id=?",
                [(i,) for i in ids],
            )
            conn.commit()
        return ids
    finally:
        conn.close()


def relist_opportunity(opportunity_id: int, new_deadline: str) -> None:
    """A posting reappearing with a real, known deadline reopens a
    'closed' or 'on_hold' opportunity - a fact update, same reasoning as
    `close_expired_opportunities` (PB-032, extended for 'on_hold' in
    PB-037). Clears lifecycle_note - whatever explained the hold no longer
    applies once there's a real deadline to act on."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET application_deadline=?, lifecycle_note=NULL, "
            "lifecycle_status=CASE WHEN lifecycle_status IN ('closed', 'on_hold') "
            "THEN NULL ELSE lifecycle_status END WHERE id=?",
            (new_deadline, opportunity_id),
        )
        conn.commit()
    finally:
        conn.close()


def put_opportunity_on_hold(opportunity_id: int, note: str | None) -> None:
    """The opportunity is genuinely still live but not currently
    actionable for an external reason (PB-037) - distinct from 'closed',
    which means it's actually dead. No deadline is cleared, since none is
    usually known yet when this is called; `relist_opportunity` is what
    moves it back to active once a real deadline is known."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET lifecycle_status='on_hold', "
            "lifecycle_note=? WHERE id=?",
            (note, opportunity_id),
        )
        conn.commit()
    finally:
        conn.close()


_CLOSEABLE_STATUSES = (None, "on_hold")


def close_opportunity(opportunity_id: int, note: str | None) -> None:
    """Manually marks an opportunity 'closed' - dead, no further action -
    for a reason other than its deadline passing (PB-041): Ingolv decided
    it, rather than `close_expired_opportunities()` deriving it from a
    date. Only ever moves an opportunity out of 'active' (NULL) or
    'on_hold' - raises if it isn't in one of those, since every other
    status (rejected/interview/awaiting_action/submitted_pending_outcome/
    already-closed) is a real recorded fact, not something to silently
    overwrite."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT lifecycle_status FROM opportunities WHERE id=?",
            (opportunity_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No opportunity #{opportunity_id}")
        current_status = row[0]
        if current_status not in _CLOSEABLE_STATUSES:
            raise ValueError(
                f"Opportunity #{opportunity_id} has lifecycle_status="
                f"{current_status!r}, not active or on_hold - refusing to "
                f"overwrite a real recorded outcome. Use "
                f"onbuild.outcome_decision --override if the classification "
                f"itself was wrong."
            )
        conn.execute(
            "UPDATE opportunities SET lifecycle_status='closed', "
            "lifecycle_note=? WHERE id=?",
            (note, opportunity_id),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_admitted_unselected() -> list[tuple]:
    """Admitted opportunities not yet chosen to actively pursue (PB-039) -
    the pool `onbuild.selection` presents. Same ranking as
    `fetch_overview` (deadline soonest first, then fit_score), but
    restricted to `selected_at IS NULL` and to opportunities still worth
    choosing among: active or on hold, not already closed, rejected, or
    further along than 'admitted' (a brief already exists)."""
    conn = connect()
    try:
        return conn.execute(
            """
            SELECT o.id, o.title, o.organisation, o.track, o.application_deadline,
                   o.lifecycle_note, e.fit_score, e.fit_tier, e.fit_summary
            FROM opportunities o
            JOIN evaluations e ON e.id = (
                SELECT MAX(id) FROM evaluations WHERE opportunity_id = o.id
            )
            WHERE e.human_decision = 'admit'
              AND o.selected_at IS NULL
              AND (o.lifecycle_status IS NULL OR o.lifecycle_status = 'on_hold')
              AND NOT EXISTS (SELECT 1 FROM briefs WHERE opportunity_id = o.id)
            ORDER BY
                CASE WHEN o.application_deadline IS NULL THEN 1 ELSE 0 END,
                o.application_deadline ASC,
                e.fit_score DESC
            """
        ).fetchall()
    finally:
        conn.close()


def mark_opportunity_selected(opportunity_id: int) -> None:
    """Records the choice to actively pursue an admitted opportunity
    (PB-039) - a fact-only gate, same reasoning as `submission_confirmation`
    and `put_opportunity_on_hold`: this isn't a content judgment, it's
    Ingolv committing effort starting now. `onbuild.agents.brief` requires
    this to be set, in addition to admission, before it will run."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET selected_at=datetime('now') WHERE id=?",
            (opportunity_id,),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_overview() -> list[tuple]:
    """The admitted-opportunities ranked list PB-022 named and never
    built, now including deadline urgency (PB-032), paused-decision
    visibility (PB-036), and 'on_hold' visibility (PB-037) - a paused
    brief/application/interview-prep, or an opportunity on hold for an
    external reason, is exactly the thing that should surface here rather
    than being forgotten, especially with a deadline attached. Only
    opportunities with a real admit decision, still active, on hold, or
    awaiting outcome (not closed, not rejected) - sorted so an
    approaching deadline always surfaces first, then by fit_score."""
    conn = connect()
    try:
        return conn.execute(
            """
            SELECT o.id, o.title, o.organisation, o.lifecycle_status,
                   o.application_deadline, o.lifecycle_note, e.fit_score, e.fit_tier,
                   (SELECT human_decision FROM briefs
                    WHERE opportunity_id = o.id ORDER BY id DESC LIMIT 1) AS latest_brief_decision,
                   (SELECT human_decision FROM applications
                    WHERE opportunity_id = o.id ORDER BY id DESC LIMIT 1) AS latest_application_decision,
                   (SELECT human_decision FROM interview_preps
                    WHERE opportunity_id = o.id ORDER BY id DESC LIMIT 1) AS latest_interview_prep_decision
            FROM opportunities o
            JOIN evaluations e ON e.id = (
                SELECT MAX(id) FROM evaluations WHERE opportunity_id = o.id
            )
            WHERE e.human_decision = 'admit'
              AND (o.lifecycle_status IS NULL
                   OR o.lifecycle_status IN ('submitted_pending_outcome', 'awaiting_action', 'on_hold'))
            ORDER BY
                CASE WHEN o.application_deadline IS NULL THEN 1 ELSE 0 END,
                o.application_deadline ASC,
                e.fit_score DESC
            """
        ).fetchall()
    finally:
        conn.close()


def insert_evaluation(
    opportunity_id: int,
    fit_summary: str,
    distinctiveness: str,
    gates_summary: str,
    countercase: str,
    requirement_matches: str,
    gaps_summary: str | None,
    fit_score: int,
    fit_tier: str,
    suggested_action: str,
    source: str,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO evaluations
                (opportunity_id, fit_summary, distinctiveness, gates_summary,
                 countercase, requirement_matches, gaps_summary,
                 fit_score, fit_tier, suggested_action, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                fit_summary,
                distinctiveness,
                gates_summary,
                countercase,
                requirement_matches,
                gaps_summary or None,
                fit_score,
                fit_tier,
                suggested_action,
                source,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_opportunity(opportunity_id: int) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, title, organisation, raw_text, source, track "
            "FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def fetch_all_opportunities() -> list[tuple]:
    """Every opportunity, oldest first - for PB-038's registry to classify
    across the whole pipeline, not just the admitted-ranked slice
    `fetch_overview` covers. Includes `selected_at` (PB-039) so the
    registry can distinguish admitted-not-yet-chosen from chosen-but-
    not-yet-briefed, `auto_captured_at` (PB-050) so it can flag an
    auto-captured, still-unconfirmed external application distinctly
    from an ordinary `submitted_pending_outcome` one, and `lead_only_at`
    (PB-052) so it can flag an unverified lead distinctly from a real
    candidate ready for evaluation."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, title, organisation, lifecycle_status, lifecycle_note, "
            "selected_at, auto_captured_at, lead_only_at FROM opportunities ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def fetch_selection_status(opportunity_id: int) -> tuple[str | None, str | None]:
    """(latest evaluation human_decision, selected_at) for one opportunity
    (PB-039) - the two facts `onbuild.agents.brief` requires before it will
    run: admitted, and chosen to actively pursue right now."""
    conn = connect()
    try:
        evaluation = conn.execute(
            "SELECT human_decision FROM evaluations WHERE opportunity_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
        opportunity = conn.execute(
            "SELECT selected_at FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        evaluation_decision = evaluation[0] if evaluation else None
        selected_at = opportunity[0] if opportunity else None
        return evaluation_decision, selected_at
    finally:
        conn.close()


def fetch_latest_evaluation_decision(opportunity_id: int) -> tuple | None:
    """Minimal counterpart to `fetch_latest_evaluation` - just enough to
    classify pipeline stage (PB-038's registry), not the full content an
    agent would need. None means no evaluation exists at all yet, distinct
    from an evaluation existing with `human_decision` still None."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, human_decision FROM evaluations "
            "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def fetch_latest_brief_decision(opportunity_id: int) -> tuple | None:
    """Minimal counterpart to `fetch_latest_brief` (PB-038's registry)."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, human_decision FROM briefs "
            "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def fetch_latest_application_decision(opportunity_id: int) -> tuple | None:
    """Minimal counterpart to `fetch_latest_application` (PB-038's
    registry) - adds `submitted_at`, which the full fetch doesn't carry,
    since stage classification needs to distinguish approved-not-yet-
    submitted from actually submitted."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, human_decision, submitted_at FROM applications "
            "WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def fetch_latest_outcome_decision(application_id: int) -> tuple | None:
    """Minimal outcome lookup for one application (PB-038's registry) -
    whether a message has been classified for it yet, and whether that
    classification has been reviewed."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, human_decision, category FROM outcomes "
            "WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (application_id,),
        ).fetchone()
    finally:
        conn.close()


def fetch_latest_evaluation(opportunity_id: int) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, fit_summary, distinctiveness, gates_summary, "
            "countercase, requirement_matches, gaps_summary, fit_score, "
            "fit_tier, suggested_action, human_decision "
            "FROM evaluations WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def lookup_company(name: str) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, name, general_profile, source, updated_at "
            "FROM companies WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()


def upsert_company(name: str, general_profile: str, source: str) -> int:
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM companies WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE companies SET general_profile = ?, source = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (general_profile, source, existing[0]),
            )
            company_id = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO companies (name, general_profile, source) "
                "VALUES (?, ?, ?)",
                (name, general_profile, source),
            )
            company_id = cur.lastrowid
        conn.commit()
        return company_id
    finally:
        conn.close()


def lookup_company_field_position(company_id: int, field: str) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, field, positioning, quality_tag, source, updated_at "
            "FROM company_field_positions WHERE company_id = ? AND field = ?",
            (company_id, field),
        ).fetchone()
    finally:
        conn.close()


def upsert_company_field_position(
    company_id: int, field: str, positioning: str, quality_tag: str, source: str
) -> int:
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM company_field_positions "
            "WHERE company_id = ? AND field = ?",
            (company_id, field),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE company_field_positions SET positioning = ?, "
                "quality_tag = ?, source = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (positioning, quality_tag, source, existing[0]),
            )
            position_id = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO company_field_positions "
                "(company_id, field, positioning, quality_tag, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (company_id, field, positioning, quality_tag, source),
            )
            position_id = cur.lastrowid
        conn.commit()
        return position_id
    finally:
        conn.close()


def lookup_company_location_presence(company_id: int, location: str) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, location, presence, quality_tag, source, updated_at "
            "FROM company_location_presence WHERE company_id = ? AND location = ?",
            (company_id, location),
        ).fetchone()
    finally:
        conn.close()


def upsert_company_location_presence(
    company_id: int, location: str, presence: str, quality_tag: str, source: str
) -> int:
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM company_location_presence "
            "WHERE company_id = ? AND location = ?",
            (company_id, location),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE company_location_presence SET presence = ?, "
                "quality_tag = ?, source = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (presence, quality_tag, source, existing[0]),
            )
            presence_id = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO company_location_presence "
                "(company_id, location, presence, quality_tag, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (company_id, location, presence, quality_tag, source),
            )
            presence_id = cur.lastrowid
        conn.commit()
        return presence_id
    finally:
        conn.close()


def insert_brief(
    opportunity_id: int,
    company_id: int | None,
    company_location_presence_id: int | None,
    company_field_position_id: int | None,
    company_profile: str,
    location_presence: str | None,
    field_positioning: str,
    position_fit: str,
    candidacy_fit_summary: str,
    strategic_approach: str,
    source: str,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO briefs
                (opportunity_id, company_id, company_location_presence_id,
                 company_field_position_id, company_profile, location_presence,
                 field_positioning, position_fit, candidacy_fit_summary,
                 strategic_approach, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                company_id,
                company_location_presence_id,
                company_field_position_id,
                company_profile,
                location_presence,
                field_positioning,
                position_fit,
                candidacy_fit_summary,
                strategic_approach,
                source,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_latest_brief(opportunity_id: int) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, company_profile, location_presence, field_positioning, "
            "position_fit, candidacy_fit_summary, strategic_approach, "
            "human_decision, revision_notes "
            "FROM briefs WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def insert_application(
    opportunity_id: int,
    brief_id: int,
    captured_application_text: str | None,
    application_format_assessment: str,
    tailored_cv: str,
    cover_letter_or_message: str,
    application_form_data: str,
    question_responses: str | None,
    portfolio_recommendation: str,
    source: str,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO applications
                (opportunity_id, brief_id, captured_application_text,
                 application_format_assessment, tailored_cv,
                 cover_letter_or_message, application_form_data,
                 question_responses, portfolio_recommendation, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                brief_id,
                captured_application_text,
                application_format_assessment,
                tailored_cv,
                cover_letter_or_message,
                application_form_data,
                question_responses,
                portfolio_recommendation,
                source,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def promote_provisional_evidence(
    artifact_type: str, opportunity_id: int
) -> tuple[list[int], list[int]]:
    """Called by the three decision CLIs (admission, brief_decision,
    application_decision) after Ingolv records an *accepting* decision
    (admit/continue/approve) - PB-026's inherited-trust promotion. Flips
    every still-'proposed' node/edge whose origin matches this artifact to
    'provisional': globally live immediately, not scoped to this
    opportunity (Ingolv's own call), pending the batch override window
    (`onbuild.digest`). A rejecting/revising/dropping decision must never
    call this - byproduct evidence with no accepted parent to inherit trust
    from stays 'proposed', on the ordinary `onbuild.review` gate."""
    conn = connect()
    try:
        node_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM evidence_nodes WHERE status='proposed' "
                "AND origin_artifact_type=? AND origin_opportunity_id=?",
                (artifact_type, opportunity_id),
            ).fetchall()
        ]
        edge_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM evidence_edges WHERE status='proposed' "
                "AND origin_artifact_type=? AND origin_opportunity_id=?",
                (artifact_type, opportunity_id),
            ).fetchall()
        ]
        if node_ids:
            conn.executemany(
                "UPDATE evidence_nodes SET status='provisional', "
                "updated_at=datetime('now') WHERE id=?",
                [(nid,) for nid in node_ids],
            )
        if edge_ids:
            conn.executemany(
                "UPDATE evidence_edges SET status='provisional', "
                "updated_at=datetime('now') WHERE id=?",
                [(eid,) for eid in edge_ids],
            )
        conn.commit()
        return node_ids, edge_ids
    finally:
        conn.close()


def fetch_provisional_evidence() -> tuple[list[tuple], list[tuple]]:
    """For `onbuild.digest` (PB-026): every node/edge currently 'provisional'
    - live evidence, awaiting the batch override window - grouped by the
    parent artifact it inherited trust from."""
    conn = connect()
    try:
        nodes = conn.execute(
            "SELECT id, node_type, quality_tag, description, "
            "origin_artifact_type, origin_opportunity_id "
            "FROM evidence_nodes WHERE status='provisional' ORDER BY id"
        ).fetchall()
        edges = conn.execute(
            "SELECT id, source_node_id, target_node_id, edge_type, quality_tag, "
            "origin_artifact_type, origin_opportunity_id "
            "FROM evidence_edges WHERE status='provisional' ORDER BY id"
        ).fetchall()
        return nodes, edges
    finally:
        conn.close()


def finalize_provisional_nodes(struck_ids: set[int]) -> tuple[int, int]:
    """For `onbuild.digest`: every currently-'provisional' node becomes
    'approved' unless its id is in `struck_ids`, in which case it becomes
    'rejected' - silence is approval, by design (PB-026)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id FROM evidence_nodes WHERE status='provisional'"
        ).fetchall()
        approved = rejected = 0
        for (node_id,) in rows:
            new_status = "rejected" if node_id in struck_ids else "approved"
            conn.execute(
                "UPDATE evidence_nodes SET status=?, updated_at=datetime('now') "
                "WHERE id=?",
                (new_status, node_id),
            )
            approved += new_status == "approved"
            rejected += new_status == "rejected"
        conn.commit()
        return approved, rejected
    finally:
        conn.close()


def finalize_provisional_edges(struck_ids: set[int]) -> tuple[int, int]:
    """Edge counterpart of finalize_provisional_nodes - see its docstring."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id FROM evidence_edges WHERE status='provisional'"
        ).fetchall()
        approved = rejected = 0
        for (edge_id,) in rows:
            new_status = "rejected" if edge_id in struck_ids else "approved"
            conn.execute(
                "UPDATE evidence_edges SET status=?, updated_at=datetime('now') "
                "WHERE id=?",
                (new_status, edge_id),
            )
            approved += new_status == "approved"
            rejected += new_status == "rejected"
        conn.commit()
        return approved, rejected
    finally:
        conn.close()


def insert_external_application(
    title: str,
    organisation: str,
    raw_text: str,
    track: str | None,
    sent_content_note: str,
    source: str,
) -> tuple[int, int, int]:
    """Registers an opportunity that was evaluated, drafted, and submitted
    entirely outside this system (PB-031) - creates the opportunity plus
    the minimal placeholder brief/application rows the schema still
    requires (applications.brief_id is NOT NULL), and marks it submitted
    immediately, since in real life it already was. Returns
    (opportunity_id, brief_id, application_id)."""
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO opportunities "
            "(title, organisation, raw_text, source, track, lifecycle_status) "
            "VALUES (?, ?, ?, ?, ?, 'submitted_pending_outcome')",
            (title, organisation, raw_text, source, track),
        )
        opportunity_id = cur.lastrowid

        placeholder = "N/A - application drafted and sent outside this system, no brief written"
        cur = conn.execute(
            "INSERT INTO briefs "
            "(opportunity_id, company_profile, field_positioning, position_fit, "
            "candidacy_fit_summary, strategic_approach, human_decision, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'continue', ?)",
            (opportunity_id, placeholder, placeholder, placeholder, placeholder, placeholder, source),
        )
        brief_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO applications "
            "(opportunity_id, brief_id, application_format_assessment, tailored_cv, "
            "cover_letter_or_message, application_form_data, portfolio_recommendation, "
            "human_decision, submitted_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'approve', datetime('now'), ?)",
            (
                opportunity_id,
                brief_id,
                "External application - drafted and sent outside this system.",
                sent_content_note,
                sent_content_note,
                "N/A - external application",
                "N/A - external application",
                source,
            ),
        )
        application_id = cur.lastrowid
        conn.commit()
        return opportunity_id, brief_id, application_id
    finally:
        conn.close()


def capture_external_application(
    title: str, organisation: str, receipt_text: str, source: str
) -> int | None:
    """Auto-captures an application confirmed sent entirely outside this
    system (PB-050) - a receipt/confirmation message for something
    never registered here at all (a quick LinkedIn Easy Apply, a direct
    email). Same insertion shape as `insert_external_application`
    (PB-031: opportunity + placeholder brief + application,
    `submitted_pending_outcome` immediately, since in real life it
    already is), but flagged via `auto_captured_at` so it surfaces for
    confirmation (`onbuild.confirm_captured_applications`) rather than
    being silently trusted the way a human's own manual registration
    already is. Deduplicated against everything already tracked, by any
    route; returns None if it's a duplicate (nothing inserted)."""
    if opportunity_exists(title, organisation):
        return None
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO opportunities "
            "(title, organisation, raw_text, source, lifecycle_status, auto_captured_at) "
            "VALUES (?, ?, ?, ?, 'submitted_pending_outcome', datetime('now'))",
            (
                title,
                organisation,
                "(posting text not available - auto-captured from a receipt "
                "confirmation, not discovered as a listing)",
                source,
            ),
        )
        opportunity_id = cur.lastrowid

        placeholder = (
            "N/A - auto-captured from a receipt confirmation; no brief was written"
        )
        cur = conn.execute(
            "INSERT INTO briefs (opportunity_id, company_profile, field_positioning, "
            "position_fit, candidacy_fit_summary, strategic_approach, human_decision, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'continue', ?)",
            (opportunity_id, placeholder, placeholder, placeholder, placeholder, placeholder, source),
        )
        brief_id = cur.lastrowid

        receipt_note = (
            f"RECEIPT MESSAGE CAPTURED (not the actual application sent):\n\n{receipt_text}"
        )
        cur = conn.execute(
            "INSERT INTO applications (opportunity_id, brief_id, application_format_assessment, "
            "tailored_cv, cover_letter_or_message, application_form_data, "
            "portfolio_recommendation, human_decision, submitted_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'approve', datetime('now'), ?)",
            (
                opportunity_id,
                brief_id,
                "Auto-captured external application - only a receipt/confirmation "
                "message was seen, not the actual application content.",
                receipt_note,
                receipt_note,
                "N/A - external application, auto-captured",
                "N/A - external application, auto-captured",
                source,
            ),
        )
        conn.commit()
        return opportunity_id
    finally:
        conn.close()


def fetch_captured_applications_pending_confirmation() -> list[tuple]:
    """Auto-captured external applications (PB-050) awaiting Ingolv's
    confirmation that they're real, not a misread -
    `onbuild.confirm_captured_applications`'s pending list."""
    conn = connect()
    try:
        return conn.execute(
            """
            SELECT o.id, o.title, o.organisation, o.auto_captured_at,
                   a.cover_letter_or_message
            FROM opportunities o
            JOIN applications a ON a.id = (
                SELECT MAX(id) FROM applications WHERE opportunity_id = o.id
            )
            WHERE o.auto_captured_at IS NOT NULL
            ORDER BY o.auto_captured_at
            """
        ).fetchall()
    finally:
        conn.close()


def confirm_captured_application(opportunity_id: int) -> None:
    """Ingolv confirms an auto-captured application is real (PB-050) -
    clears `auto_captured_at`; from this point it's indistinguishable
    from any other `submitted_pending_outcome` opportunity."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET auto_captured_at=NULL WHERE id=?",
            (opportunity_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reject_captured_application(opportunity_id: int, note: str | None) -> None:
    """Ingolv rejects an auto-captured application as a misread (PB-050)
    - not a real application, so it's closed with a clear note rather
    than left masquerading as a live `submitted_pending_outcome`
    opportunity. Deliberately not routed through
    `onbuild.close_opportunity`'s guard, which exists to protect a
    genuinely real recorded outcome from being overwritten - an
    unconfirmed auto-capture was never confirmed real in the first
    place, so there's nothing there to protect."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE opportunities SET lifecycle_status='closed', "
            "lifecycle_note=?, auto_captured_at=NULL WHERE id=?",
            (
                note or "Auto-capture rejected - misread, not an actual application.",
                opportunity_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_unmatched_message(unmatched_id: int) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, uid, sender, subject, message_text, status "
            "FROM unmatched_mailbox_messages WHERE id = ?",
            (unmatched_id,),
        ).fetchone()
    finally:
        conn.close()


def resolve_unmatched_message(unmatched_id: int) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE unmatched_mailbox_messages SET status='resolved' WHERE id=?",
            (unmatched_id,),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_confirmed_interview_outcome(opportunity_id: int) -> tuple | None:
    """The most recent outcome for this opportunity actually applied
    (PB-040) as 'interview' - the real invitation message the
    interview-preparation agent reads for who the interview is scheduled
    with (PB-035). Keyed on `applied_category`, not `human_decision`:
    most interview outcomes are applied directly by the outcome agent
    now and are never reviewed by a human at all, so `human_decision`
    stays NULL for them - `applied_category` is the fact that matters."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, application_id, captured_message_text "
            "FROM outcomes WHERE opportunity_id = ? "
            "AND applied_category = 'interview' "
            "ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


def insert_interview_prep(
    opportunity_id: int,
    brief_id: int,
    outcome_id: int | None,
    interviewer_research: str,
    office_leadership_research: str,
    talking_points: str,
    requirement_coverage: str,
    source: str,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO interview_preps
                (opportunity_id, brief_id, outcome_id, interviewer_research,
                 office_leadership_research, talking_points, requirement_coverage, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                brief_id,
                outcome_id,
                interviewer_research,
                office_leadership_research,
                talking_points,
                requirement_coverage,
                source,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_mailbox_last_uid(folder: str = "INBOX") -> int:
    """Per-folder watermark (PB-046) - IMAP UIDs are only unique within
    one folder, so INBOX and a label like "LinkedIn Jobs" each need
    their own progress marker."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT last_processed_uid FROM mailbox_state WHERE folder = ?",
            (folder,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def update_mailbox_last_uid(uid: int, folder: str = "INBOX") -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO mailbox_state (folder, last_processed_uid, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(folder) DO UPDATE SET last_processed_uid=excluded.last_processed_uid, "
            "updated_at=datetime('now')",
            (folder, uid),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_opportunities_awaiting_outcome() -> list[tuple]:
    """Candidates `onbuild.mailbox` can match a live message against -
    opportunities actually awaiting a reply (PB-030), plus, since
    PB-049, an approved draft never yet confirmed submitted. The second
    case matters because a receipt or outcome message about one of
    these IS itself the fact that submission happened - the authorship
    principle (PB-038/040) applied one step earlier: Ingolv approving a
    draft and then never running `onbuild.submission_confirmation` (or
    simply moving on and not coming back to it) shouldn't make the
    system blind to a reply that already proves what happened."""
    conn = connect()
    try:
        return conn.execute(
            """
            SELECT o.id, o.title, o.organisation FROM opportunities o
            WHERE o.lifecycle_status IN ('submitted_pending_outcome', 'awaiting_action')
               OR (
                   o.lifecycle_status IS NULL
                   AND EXISTS (
                       SELECT 1 FROM applications a
                       WHERE a.id = (SELECT MAX(id) FROM applications WHERE opportunity_id = o.id)
                         AND a.opportunity_id = o.id
                         AND a.human_decision = 'approve'
                         AND a.submitted_at IS NULL
                   )
               )
            """
        ).fetchall()
    finally:
        conn.close()


def insert_unmatched_message(
    uid: int,
    sender: str | None,
    subject: str | None,
    message_text: str,
    candidate_opportunity_ids: str,
    received_at: str | None,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO unmatched_mailbox_messages
                (uid, sender, subject, message_text, candidate_opportunity_ids, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, sender, subject, message_text, candidate_opportunity_ids, received_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_opportunity_lifecycle_status(opportunity_id: int) -> str | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT lifecycle_status FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def insert_outcome(
    application_id: int,
    opportunity_id: int,
    captured_message_text: str,
    submission_confirmed: bool | None,
    category: str,
    rationale: str,
    suggested_next_step: str | None,
    source: str,
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO outcomes
                (application_id, opportunity_id, captured_message_text,
                 submission_confirmed, category, rationale,
                 suggested_next_step, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_id,
                opportunity_id,
                captured_message_text,
                None if submission_confirmed is None else int(submission_confirmed),
                category,
                rationale,
                suggested_next_step,
                source,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# The authorship principle (PB-038, implemented PB-040): past submission,
# the recipient authors this fact, not Ingolv - 'receipt_confirmation' and
# 'unclear' are intentionally absent. 'receipt_confirmation' needs no
# lifecycle_status change (submission_confirmation already set it);
# 'unclear' has no fact yet to apply - it's the sole category that still
# requires a human decision (`onbuild.outcome_decision`) before anything
# is applied. Shared between the outcome agent's direct-apply path and
# `onbuild.outcome_decision`'s resolve/override paths, so the mapping
# can't drift between the two.
OUTCOME_LIFECYCLE_STATUS = {
    "interview": "interview",
    "rejection": "rejected",
    "further_info": "awaiting_action",
    "other_request": "awaiting_action",
}


def apply_outcome(outcome_id: int, opportunity_id: int, category: str) -> tuple[str | None, bool]:
    """Applies the real-world fact a classified message represents
    directly - the opportunity's own `lifecycle_status` if this category
    maps to one, and always `outcomes.applied_category`/`applied_at` so
    the row is no longer awaiting a decision.

    If the matched application was never confirmed submitted (PB-049 -
    approved, but `onbuild.submission_confirmation` never ran, or Ingolv
    simply moved on and didn't come back to it), this message's own
    arrival already proves submission happened - the authorship
    principle (PB-038/040) applied one step earlier in the funnel - so
    `applications.submitted_at` is backfilled first, before the
    category's own status is applied on top.

    Returns (new lifecycle_status, or None if this category needs none;
    whether a submission was just backfilled). Never call this for
    'unclear' - there is no fact yet to apply."""
    new_status = OUTCOME_LIFECYCLE_STATUS.get(category)
    conn = connect()
    try:
        outcome_row = conn.execute(
            "SELECT application_id FROM outcomes WHERE id=?", (outcome_id,)
        ).fetchone()
        application_id = outcome_row[0] if outcome_row else None

        backfilled = False
        if application_id is not None:
            app_row = conn.execute(
                "SELECT submitted_at FROM applications WHERE id=?", (application_id,)
            ).fetchone()
            if app_row and app_row[0] is None:
                conn.execute(
                    "UPDATE applications SET submitted_at=datetime('now') WHERE id=?",
                    (application_id,),
                )
                conn.execute(
                    "UPDATE opportunities SET lifecycle_status='submitted_pending_outcome' WHERE id=?",
                    (opportunity_id,),
                )
                backfilled = True

        if new_status:
            conn.execute(
                "UPDATE opportunities SET lifecycle_status=? WHERE id=?",
                (new_status, opportunity_id),
            )
        conn.execute(
            "UPDATE outcomes SET applied_category=?, applied_at=datetime('now') WHERE id=?",
            (category, outcome_id),
        )
        conn.commit()
        return new_status, backfilled
    finally:
        conn.close()


def fetch_latest_application(opportunity_id: int) -> tuple | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, tailored_cv, cover_letter_or_message, "
            "application_form_data, question_responses, "
            "portfolio_recommendation, human_decision, revision_notes "
            "FROM applications WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()


_GRAPH_LISTING_DESCRIPTION_CHAR_LIMIT = 100


def _truncate_for_listing(text: str) -> str:
    if len(text) <= _GRAPH_LISTING_DESCRIPTION_CHAR_LIMIT:
        return text
    return text[:_GRAPH_LISTING_DESCRIPTION_CHAR_LIMIT].rstrip() + "... [truncated in this overview]"


def fetch_graph_listing(full: bool = False) -> str:
    """The shared read behind every agent's `list_evidence_graph` tool
    (curator, evaluation, brief, drafting, outcome, scan, interview_prep).

    PB-051: an unfiltered, untruncated dump of the whole graph grew to
    ~55KB as real evidence accumulated - past whatever size limit the
    tool-result mechanism allows, silently redirecting the output to a
    file no agent session has any tool to read. 15 of one 47-opportunity
    evaluation batch explicitly reported seeing only the first handful
    of nodes and zero identity-core facts as a result - every gate that
    depends on knowing who Ingolv actually is (location, work
    authorization, everything) was blind, not just under-informed.

    Default (`full=False`, used by every agent except curator): only
    `approved`/`provisional` nodes and edges, and only `is_current`
    identity facts - the tier every agent's own instructions already
    say is the only real evidence anyway, so dropping `proposed`/
    `rejected`/superseded rows here loses nothing they were supposed to
    rely on. `full=True` (curator only, which genuinely needs to see
    `proposed`/`rejected` rows to avoid re-proposing a duplicate or
    missing a correction) keeps every status.

    Every description is truncated to a plain, conservative length
    regardless of `full` - descriptions, not row count, drove most of
    the actual byte total, and no agent here needs a node's complete
    text just to know it exists and roughly what it says; the node's
    own id is always shown, so a genuine need for the exact original
    text is a data question, not something this overview has to carry."""
    conn = connect()
    try:
        node_query = "SELECT id, node_type, quality_tag, status, description FROM evidence_nodes"
        edge_query = (
            "SELECT id, source_node_id, target_node_id, edge_type, quality_tag, status "
            "FROM evidence_edges"
        )
        fact_query = "SELECT id, key, value, status, is_current FROM identity_core"
        if not full:
            node_query += " WHERE status IN ('approved', 'provisional')"
            edge_query += " WHERE status IN ('approved', 'provisional')"
            fact_query += " WHERE is_current = 1"
        nodes = conn.execute(node_query + " ORDER BY id").fetchall()
        edges = conn.execute(edge_query + " ORDER BY id").fetchall()
        facts = conn.execute(fact_query + " ORDER BY id").fetchall()
    finally:
        conn.close()

    node_header = "EVIDENCE NODES:" if full else "EVIDENCE NODES (approved/provisional only):"
    lines = [node_header]
    lines += [
        f"#{node_id} [{status}] [{node_type}/{quality_tag}] {_truncate_for_listing(description)}"
        for node_id, node_type, quality_tag, status, description in nodes
    ] or ["(none yet)"]

    edge_header = "\nEVIDENCE EDGES:" if full else "\nEVIDENCE EDGES (approved/provisional only):"
    lines.append(edge_header)
    lines += [
        f"#{edge_id} [{status}] {source_id} --[{edge_type}/{quality_tag}]--> {target_id}"
        for edge_id, source_id, target_id, edge_type, quality_tag, status in edges
    ] or ["(none yet)"]

    lines.append("\nIDENTITY CORE:")
    lines += [
        f"#{fact_id} [{status}, {'current' if is_current else 'not current'}] "
        f"{key}: {_truncate_for_listing(value)}"
        for fact_id, key, value, status, is_current in facts
    ] or ["(none yet)"]

    return "\n".join(lines)
