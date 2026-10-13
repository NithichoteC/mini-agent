"""Knowledge graph extraction and retrieval for semantic relationship extraction.

The graph_library implements a GraphRAG-style retrieval layer: extract
semantic paths (entity relationships) from text chunks, build a property
graph, and retrieve relevant subgraphs (multi-hop neighborhoods) for
context-augmented LLM queries.

Pipeline:

    text/chunks -> extract_paths -> normalize_entities -> build_graph
                -> retrieve_subgraph -> graph_to_context

This is the course's graph_library adapted to the mini-agent core,
with LLM-handler integration and Thai language support.

Public API:
    - extract_paths(text, llm=None, schema=None, source_chunk_id=None)
    - extract_paths_batch(chunks, llm=None, schema=None, max_chars=6000)
    - normalize_entities(paths, aliases=None)
    - normalize_entity_key(text) -- deterministic matching key helper
    - build_entity_alias_report(paths, aliases=None)
    - build_graph(paths)
    - retrieve_subgraph(graph, query=None, seed_entities=None, depth=1,
                        seeding_mode="hybrid", seed_match_mode="exact",
                        seed_aliases=None)
    - resolve_seed_entities(graph, seed_entities, aliases=None,
                            seed_match_mode="normalized")
    - SEEDING_MODES, SEED_MATCH_MODES (frozensets)
    - graph_to_context(subgraph)
    - validate_path(path), validate_paths(paths) -- from schema module
"""

from .extractor import extract_paths, extract_paths_batch
from .formatter import graph_to_context
from .graph_store import build_graph
from .normalizer import build_entity_alias_report, normalize_entities, normalize_entity_key
from .retriever import SEED_MATCH_MODES, SEEDING_MODES, resolve_seed_entities, retrieve_subgraph
from .schema import validate_path, validate_paths

__all__ = [
    "extract_paths",
    "extract_paths_batch",
    "normalize_entities",
    "normalize_entity_key",
    "build_entity_alias_report",
    "build_graph",
    "retrieve_subgraph",
    "resolve_seed_entities",
    "SEEDING_MODES",
    "SEED_MATCH_MODES",
    "graph_to_context",
    "validate_path",
    "validate_paths",
]
