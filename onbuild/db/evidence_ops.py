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
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO evidence_nodes
                (node_type, quality_tag, description, attributes, status, source)
            VALUES (?, ?, ?, ?, 'proposed', ?)
            """,
            (node_type, quality_tag, description, attributes or None, source),
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
) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO evidence_edges
                (source_node_id, target_node_id, edge_type, quality_tag, status, source)
            VALUES (?, ?, ?, ?, 'proposed', ?)
            """,
            (source_node_id, target_node_id, edge_type, quality_tag, source),
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
