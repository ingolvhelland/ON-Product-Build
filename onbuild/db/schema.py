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
CREATE TABLE IF NOT EXISTS evidence_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type TEXT NOT NULL,            -- e.g. 'claim', 'portfolio_artifact', 'gap'
    quality_tag TEXT NOT NULL,          -- direct / transferable / developing / hypothetical / absent_unknown
    description TEXT NOT NULL,
    attributes TEXT,                    -- JSON payload, fields specific to node_type
    status TEXT NOT NULL DEFAULT 'proposed',  -- proposed / approved / rejected
    source TEXT,                        -- where this came from, e.g. 'curator: cv_parse 2026-09-11'
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
"""


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the schema if it doesn't already exist. Safe to call repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
