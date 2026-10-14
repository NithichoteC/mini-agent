"""Convert a retrieved subgraph into an LLM-readable / human-readable string.

One job: subgraph dict -> context string. No retrieval logic, no LLM calls.
"""

from __future__ import annotations


def graph_to_context(subgraph: dict) -> str:
    """Render a subgraph (as returned by `retrieve_subgraph`) as text.

    Preserves, per edge: the relation triple, evidence, confidence, and
    source_chunk_id, in the order edges appear in the subgraph (which is
    the BFS expansion order from `retriever.retrieve_subgraph`).
    """
    edges = subgraph.get("edges", []) if isinstance(subgraph, dict) else []

    if not edges:
        return "Graph evidence: (no relevant graph context found)"

    lines = ["Graph evidence:"]
    for index, edge in enumerate(edges, start=1):
        source = edge.get("source", "?")
        relation = edge.get("relation", "?")
        target = edge.get("target", "?")
        evidence = edge.get("evidence", "")
        confidence = edge.get("confidence")
        source_chunk_id = edge.get("source_chunk_id")

        lines.append(f"{index}. {source} --{relation}--> {target}")
        if evidence:
            lines.append(f"   Evidence: {evidence}")
        lines.append(f"   Source: {source_chunk_id}")
        lines.append(f"   Confidence: {confidence}")

    return "\n".join(lines)
