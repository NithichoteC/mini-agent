"""Build a plain-Python property graph from semantic paths.

No NetworkX, no Neo4j, no database -- just:

    {"nodes": {entity_id: {"id", "label", "properties"}}, "edges": [...]}

This is the only place that constructs the graph representation. Retrieval
(`retriever.py`) reads this structure but does not build it.
"""

from __future__ import annotations

from .schema import validate_path


def _add_node(nodes: dict, entity_id: str) -> None:
    if entity_id not in nodes:
        nodes[entity_id] = {"id": entity_id, "label": entity_id, "properties": {}}


def build_graph(paths: list[dict]) -> dict:
    """Build a property graph from a list of semantic paths.

    Every path is re-validated here (cheap, and `graph_library` never
    silently accepts malformed paths regardless of which function is
    handed them). A malformed path raises `ValueError` rather than being
    dropped quietly, since by the time paths reach `build_graph` they are
    expected to already be clean (extracted + normalized).
    """
    graph: dict = {"nodes": {}, "edges": []}

    for raw_path in paths:
        cleaned, reason = validate_path(raw_path)
        if cleaned is None:
            raise ValueError(f"build_graph received a malformed path: {reason} (path={raw_path!r})")

        _add_node(graph["nodes"], cleaned["source"])
        _add_node(graph["nodes"], cleaned["target"])

        graph["edges"].append(
            {
                "source": cleaned["source"],
                "relation": cleaned["relation"],
                "target": cleaned["target"],
                "evidence": cleaned["evidence"],
                "confidence": cleaned["confidence"],
                "source_chunk_id": cleaned["source_chunk_id"],
            }
        )

    return graph
