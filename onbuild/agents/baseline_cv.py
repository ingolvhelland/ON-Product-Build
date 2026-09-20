"""
Baseline CV generator - the second half of PB-010's round-trip acceptance
test: generate the saddle's baseline CV from whatever evidence is currently
approved, then compare it against the original CV that was fed to the
curator.

Unlike the curator, this is not an agent. There is nothing to look up or
decide interactively - every fact it may use is already known upfront (all
currently `approved` evidence), so the whole job is synthesizing text from a
complete, given input in one pass. That is the actual test for whether a
task needs an agent (a tool-use loop, deciding what to do next based on what
it finds) versus a single request/response call to Claude. `curator.py` is a
real agent because it must call `list_evidence_graph` and decide, tool call
by tool call, what the graph already contains before proposing something
new. This script has no such loop, so it calls the plain Anthropic API
directly (the `anthropic` package), not the Claude Agent SDK.

Per PB-015: presentation choices - what to compress, what to keep separate,
how much prominence to give something - are generation-time judgments, not
properties of the evidence itself. The model makes those choices fresh each
time this runs, from whatever is currently approved. The baseline CV is
deliberately general-purpose: not written for any specific opportunity or
track (that tailoring is a later agent's job, not this one's).

Only `status='approved'` evidence is used - never `proposed` or `rejected`
content, and only identity facts that are both `approved` and `is_current`.
"""

import argparse
import sqlite3
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from onbuild.db.schema import DB_PATH

load_dotenv()

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You write a baseline CV/resume from a structured set of approved evidence
about one person. This is a general-purpose document, not tailored to any
specific job, employer, or track - it should read as the best honest,
complete account of this person's background.

Use only the evidence given to you. Never invent, embellish, or infer facts
beyond what is stated or directly implied by the evidence and its
relationships. If two items are connected by an edge (for example one
specifies a sub-period of another, or one corrects another), use that
relationship to decide how to group, order, or phrase them - do not just
list every node as its own bullet regardless of how it connects to others.

Decide compression and prominence yourself: some items belong grouped
together under one role or heading, others deserve their own line. Quality
tags matter - "hypothetical" or "developing" evidence should read as such,
not stated as flatly as "direct" evidence.

Write it as a real CV: a header with the person's name, contact details and
identity facts, then clearly labelled sections (e.g. Profile, Experience,
Education, Skills) in a sensible order. If the person's name is not present
anywhere in the evidence given to you, leave the header's name line as
"[NAME NOT FOUND IN APPROVED EVIDENCE]" rather than guessing or leaving it
blank - do not invent it. Output the CV text only - no commentary before or
after it.
"""


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _load_approved_graph() -> str:
    conn = _connect()
    try:
        nodes = conn.execute(
            "SELECT id, node_type, quality_tag, description, attributes "
            "FROM evidence_nodes WHERE status = 'approved' ORDER BY id"
        ).fetchall()
        edges = conn.execute(
            "SELECT source_node_id, target_node_id, edge_type, quality_tag "
            "FROM evidence_edges WHERE status = 'approved' ORDER BY id"
        ).fetchall()
        facts = conn.execute(
            "SELECT key, value FROM identity_core "
            "WHERE status = 'approved' AND is_current = 1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    lines = ["EVIDENCE NODES:"]
    lines += [
        f"#{node_id} [{node_type}/{quality_tag}] {description}"
        + (f" | attributes: {attributes}" if attributes else "")
        for node_id, node_type, quality_tag, description, attributes in nodes
    ]

    lines.append("\nEVIDENCE EDGES:")
    lines += [
        f"#{source_id} --[{edge_type}/{quality_tag}]--> #{target_id}"
        for source_id, target_id, edge_type, quality_tag in edges
    ] or ["(none)"]

    lines.append("\nIDENTITY CORE:")
    lines += [f"{key}: {value}" for key, value in facts] or ["(none)"]

    return "\n".join(lines)


def generate_baseline_cv() -> str:
    graph_text = _load_approved_graph()
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": graph_text}],
    )
    return "".join(
        block.text for block in message.content if block.type == "text"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the baseline CV from currently approved evidence."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the CV to this file instead of stdout.",
    )
    args = parser.parse_args()
    cv_text = generate_baseline_cv()
    if args.out:
        args.out.write_text(cv_text)
        print(f"Baseline CV written to {args.out}")
    else:
        print(cv_text)


if __name__ == "__main__":
    main()
