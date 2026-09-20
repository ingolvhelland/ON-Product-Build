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
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO opportunities (title, organisation, raw_text, source, track)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, organisation, raw_text, source, track),
        )
        conn.commit()
        return cur.lastrowid
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


def fetch_mailbox_last_uid() -> int:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT last_processed_uid FROM mailbox_state WHERE id = 1"
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def update_mailbox_last_uid(uid: int) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO mailbox_state (id, last_processed_uid, updated_at) "
            "VALUES (1, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET last_processed_uid=excluded.last_processed_uid, "
            "updated_at=datetime('now')",
            (uid,),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_opportunities_awaiting_outcome() -> list[tuple]:
    """Candidates onbuild.mailbox can match a live message against - only
    opportunities actually awaiting a reply (PB-030)."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, title, organisation FROM opportunities "
            "WHERE lifecycle_status IN ('submitted_pending_outcome', 'awaiting_action')"
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


def fetch_graph_listing() -> str:
    conn = connect()
    try:
        nodes = conn.execute(
            "SELECT id, node_type, quality_tag, status, description "
            "FROM evidence_nodes ORDER BY id"
        ).fetchall()
        edges = conn.execute(
            "SELECT id, source_node_id, target_node_id, edge_type, quality_tag, status "
            "FROM evidence_edges ORDER BY id"
        ).fetchall()
        facts = conn.execute(
            "SELECT id, key, value, status, is_current FROM identity_core ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    lines = ["EVIDENCE NODES:"]
    lines += [
        f"#{node_id} [{status}] [{node_type}/{quality_tag}] {description}"
        for node_id, node_type, quality_tag, status, description in nodes
    ] or ["(none yet)"]

    lines.append("\nEVIDENCE EDGES:")
    lines += [
        f"#{edge_id} [{status}] {source_id} --[{edge_type}/{quality_tag}]--> {target_id}"
        for edge_id, source_id, target_id, edge_type, quality_tag, status in edges
    ] or ["(none yet)"]

    lines.append("\nIDENTITY CORE:")
    lines += [
        f"#{fact_id} [{status}, {'current' if is_current else 'not current'}] "
        f"{key}: {value}"
        for fact_id, key, value, status, is_current in facts
    ] or ["(none yet)"]

    return "\n".join(lines)
