"""
Digest CLI - the batch override window over byproduct evidence (PB-026).

Byproduct evidence (a node/edge an evaluation, brief, or drafting agent
surfaces while doing its actual job, distinct from the curator's primary
intake) is promoted to status='provisional' the moment its parent artifact
is itself accepted by Ingolv - admission.py's 'admit', brief_decision.py's
'continue', application_decision.py's 'approve'. It is live evidence
immediately, globally, from that point (Ingolv's own call: deciding on the
parent artifact already put human eyes on the substance the byproduct
evidence is drawn from - "what the update is a byproduct of will have been
explicitly approved... that is security built into it").

This tool is the explicit override on that inherited trust, not a second
copy of onbuild.review's per-item gate: every 'provisional' item is listed
together, in one batch, and anything not struck here becomes 'approved' on
this run - silence is approval, by design. Primary intake (curator-proposed,
no parent artifact) never appears here; it stays on onbuild.review's
explicit per-item approve/reject, unchanged, because it has no parent
decision to inherit trust from.

Run it directly:

    python -m onbuild.digest
"""

from onbuild.db import evidence_ops


def main() -> None:
    nodes, edges = evidence_ops.fetch_provisional_evidence()
    if not nodes and not edges:
        print("No provisional evidence awaiting digest.")
        return

    print("=== Provisional evidence (approved by default unless struck) ===")

    if nodes:
        print("\nNODES:")
        for (
            node_id,
            node_type,
            quality_tag,
            description,
            origin_artifact_type,
            origin_opportunity_id,
        ) in nodes:
            print(
                f"  node:{node_id} [{node_type}/{quality_tag}] "
                f"(from {origin_artifact_type} on opportunity #{origin_opportunity_id})"
            )
            print(f"    {description}")

    if edges:
        print("\nEDGES:")
        for (
            edge_id,
            source_id,
            target_id,
            edge_type,
            quality_tag,
            origin_artifact_type,
            origin_opportunity_id,
        ) in edges:
            print(
                f"  edge:{edge_id} {source_id} --[{edge_type}/{quality_tag}]--> "
                f"{target_id} (from {origin_artifact_type} on opportunity "
                f"#{origin_opportunity_id})"
            )

    raw = input(
        "\nStrike any items? Enter comma-separated ids (e.g. 'node:51, edge:52'), "
        "or press Enter to approve everything above > "
    ).strip()

    struck_nodes: set[int] = set()
    struck_edges: set[int] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        kind, _, raw_id = token.partition(":")
        if not raw_id.isdigit():
            print(f"  (ignoring unrecognised token: {token!r})")
            continue
        if kind == "node":
            struck_nodes.add(int(raw_id))
        elif kind == "edge":
            struck_edges.add(int(raw_id))
        else:
            print(f"  (ignoring unrecognised token: {token!r})")

    approved_n, rejected_n = evidence_ops.finalize_provisional_nodes(struck_nodes)
    approved_e, rejected_e = evidence_ops.finalize_provisional_edges(struck_edges)

    print(
        f"\n=== Summary ===\n"
        f"Nodes: {approved_n} approved, {rejected_n} struck/rejected\n"
        f"Edges: {approved_e} approved, {rejected_e} struck/rejected"
    )


if __name__ == "__main__":
    main()
