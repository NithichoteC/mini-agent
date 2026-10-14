"""Retrieve a relevant subgraph (neighborhood / causal chain) from a graph.

This is the function that is supposed to beat vanilla top-k vector
retrieval on multi-hop questions: instead of ranking independent chunks by
similarity, it starts from seed entities (or query keyword matches) and
walks relation edges outward by `depth` hops, returning the connected
nodes/edges -- e.g. the full causal chain from one event to a much later,
lexically-dissimilar consequence.
"""

from __future__ import annotations

from .normalizer import normalize_entity_key

EMPTY_SUBGRAPH = {"nodes": {}, "edges": []}

# Phase 7: controlled graph seeding modes. "hybrid" is the default and
# reproduces the exact pre-Phase-7 behavior (seed_entities + query keyword
# seeding combined) so existing callers that never pass `seeding_mode` are
# unaffected.
SEEDING_MODES = frozenset({"entity_only", "query_only", "hybrid"})

# Phase 8: canonical seed matching modes for `seed_entities` resolution.
# "exact" is the default at the `retrieve_subgraph()` level (preserves
# pre-Phase-8 behavior byte-for-byte); `resolve_seed_entities()` itself
# defaults to "normalized", per the Phase 8 build brief.
SEED_MATCH_MODES = frozenset({"exact", "normalized", "alias"})

# A small, generic stopword list for query keyword matching. Deliberately
# generic (not corpus-specific) so it doesn't quietly start excluding real
# entity/relation words later.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "did", "do",
        "does", "for", "from", "had", "has", "have", "how", "if", "in",
        "into", "is", "it", "its", "no", "not", "of", "on", "or", "that",
        "the", "their", "then", "there", "these", "they", "this", "to",
        "was", "were", "what", "when", "where", "which", "who", "whom",
        "why", "will", "with",
    }
)


def _tokenize(text: str) -> list[str]:
    """Tokenize text for query keyword matching, with Thai segmentation support."""
    try:
        from agentic_ai.retrieval.rag import tokenize
        tokens = tokenize(text)
        # Filter: keep tokens longer than 2 chars, or all Thai tokens >= 2 chars
        # Thai tokens can be 1 char, but non-Thai must be > 2
        import re
        result = []
        for t in tokens:
            # Check if token contains Thai characters
            if re.search(r"[฀-๿]", t):
                # Thai token: keep if len >= 2
                if len(t) >= 2:
                    result.append(t)
            else:
                # Non-Thai token: keep if len > 2 and not in stopwords
                if len(t) > 2 and t not in _STOPWORDS:
                    result.append(t)
        return result
    except ImportError:
        # Fallback to simple regex if agentic_ai.retrieval.rag is not available
        import re
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _term_matches(term: str, haystack: str) -> bool:
    if term in haystack:
        return True
    # Light stemming-by-prefix so "resuming" can still match "resumed", etc.
    if len(term) >= 5 and term[:5] in haystack:
        return True
    return False


def _seeds_from_entities(nodes: dict, seed_entities: list[str]) -> set[str]:
    node_lookup = {node_id.lower(): node_id for node_id in nodes}
    seeds = set()
    for seed in seed_entities:
        if not isinstance(seed, str):
            continue
        match = node_lookup.get(" ".join(seed.split()).strip().lower())
        if match:
            seeds.add(match)
    return seeds


def resolve_seed_entities(
    graph: dict,
    seed_entities: list[str] | None,
    aliases: dict[str, list[str]] | None = None,
    seed_match_mode: str = "normalized",
) -> set[str]:
    """Resolve caller-provided `seed_entities` strings to actual node labels.

    This is Phase 8's canonical seed matching layer: it decides which graph
    node labels count as a match for a caller's seed string, *before* BFS
    depth expansion ever runs. It never does substring or semantic/fuzzy
    matching -- only the three explicitly scoped modes below.

    `seed_match_mode`:
        "exact"      -- case-insensitive exact node-label match only (the
                        pre-Phase-8 behavior, identical to the original
                        private `_seeds_from_entities()`).
        "normalized" -- deterministic matching-key comparison, using the
                        same key `graph_library.normalizer.normalize_entity_key`
                        (and therefore `normalize_entities()`) uses: collapse
                        whitespace, lowercase, treat hyphen and space as
                        equivalent, strip one leading weak article
                        ("the"/"a"/"an"). E.g. "the Project Lotus" and
                        "Project-Lotus" both resolve to the node
                        "Project Lotus" if that label exists in the graph.
        "alias"      -- "normalized" matching, plus explicit `aliases`
                        (`{seed_string: [alias_node_label, ...]}`). Every
                        declared alias is itself matched against node labels
                        via the same normalized key. Alias map keys are also
                        compared via the normalized key (not raw string
                        equality), so `aliases` is exactly as
                        article/case/whitespace/hyphen-tolerant as the rest
                        of this function -- but it never invents an
                        equivalence the caller did not declare.

    Never substring-matches: "Lotus" does not match "Project Lotus", and
    "audit" does not match "compliance audit", under any mode.

    Returns the set of actual node-label strings (graph keys) that matched
    -- not the caller's input strings. Raises `ValueError` if
    `seed_match_mode` is not one of "exact", "normalized", "alias".
    """
    if seed_match_mode not in SEED_MATCH_MODES:
        raise ValueError(
            f"Invalid seed_match_mode {seed_match_mode!r}. "
            f"Allowed modes: {sorted(SEED_MATCH_MODES)}"
        )

    nodes = graph.get("nodes", {})
    if not seed_entities:
        return set()

    if seed_match_mode == "exact":
        return _seeds_from_entities(nodes, seed_entities)

    # "normalized" and "alias" both key off the same deterministic matching
    # key used by graph_library.normalizer -- build node_label lookup once.
    key_to_node: dict[str, str] = {}
    for node_id in nodes:
        key_to_node.setdefault(normalize_entity_key(node_id), node_id)

    normalized_aliases: dict[str, list[str]] = {}
    if seed_match_mode == "alias" and aliases:
        for alias_key, variants in aliases.items():
            normalized_aliases[normalize_entity_key(str(alias_key))] = list(variants or [])

    seeds: set[str] = set()
    for seed in seed_entities:
        if not isinstance(seed, str):
            continue
        seed_key = normalize_entity_key(seed)
        match = key_to_node.get(seed_key)
        if match:
            seeds.add(match)

        if seed_match_mode == "alias":
            for variant in normalized_aliases.get(seed_key, []):
                if not isinstance(variant, str):
                    continue
                variant_match = key_to_node.get(normalize_entity_key(variant))
                if variant_match:
                    seeds.add(variant_match)

    return seeds


def _seeds_from_query(edges: list[dict], query: str) -> set[str]:
    terms = _tokenize(query)
    if not terms:
        return set()
    seeds = set()
    for edge in edges:
        haystack = " ".join(
            [
                str(edge.get("source", "")),
                str(edge.get("relation", "")),
                str(edge.get("target", "")),
                str(edge.get("evidence", "")),
            ]
        ).lower()
        if any(_term_matches(term, haystack) for term in terms):
            seeds.add(edge["source"])
            seeds.add(edge["target"])
    return seeds


def retrieve_subgraph(
    graph: dict,
    query: str | None = None,
    seed_entities: list[str] | None = None,
    depth: int = 1,
    seeding_mode: str = "hybrid",
    seed_match_mode: str = "exact",
    seed_aliases: dict[str, list[str]] | None = None,
) -> dict:
    """Retrieve a connected subgraph reachable from seed entities/query.

    - `seed_entities`: matched to node labels according to `seed_match_mode`
      (see below) and become seeds.
    - `query`: simple keyword matching against entity labels and edge text
      (source/relation/target/evidence) also contributes seeds.
    - `depth`: number of hops to expand outward from the seed set. Each hop
      pulls in any edge touching the current frontier (undirected), so a
      causal chain `A -> B -> C -> D` is recoverable from seed `A` with
      `depth >= 3`.
    - `seeding_mode`: controls which of the two seed sources above are used
      to build the *initial* seed set (before depth expansion). One of:

        "entity_only" -- use only `seed_entities`; `query` (even if provided)
            is never used for keyword seeding.
        "query_only"  -- use only `query` keyword seeding; `seed_entities`
            (even if provided) is ignored, and `seed_match_mode`/
            `seed_aliases` have no effect.
        "hybrid"      -- (default) use both, exactly as before Phase 7. This
            is the pre-existing behavior and remains the default so callers
            that never pass `seeding_mode` are unaffected.

      Depth traversal and the return shape are unchanged by `seeding_mode`;
      it only controls which sources may contribute to the initial seed set.

    - `seed_match_mode` (Phase 8): controls how `seed_entities` strings are
      matched to actual node labels -- see
      `graph_library.retriever.resolve_seed_entities` for the full
      definitions of "exact" (default here, preserves pre-Phase-8 behavior
      byte-for-byte), "normalized", and "alias". Only affects the
      `seed_entities` source; never affects `query` keyword seeding. Ignored
      entirely when `seeding_mode="query_only"` (there are no seed_entities
      to match in that mode).
    - `seed_aliases` (Phase 8): optional explicit alias map,
      `{seed_string: [alias_node_label, ...]}`, only used when
      `seed_match_mode="alias"`. Has no effect under any other
      `seed_match_mode`.

    Returns `{"nodes": {...}, "edges": [...]}`. Returns an empty subgraph if
    no seeds are found.

    Raises:
        ValueError: if `seeding_mode` is not one of "entity_only",
            "query_only", "hybrid", or if `seed_match_mode` is not one of
            "exact", "normalized", "alias".
    """
    if seeding_mode not in SEEDING_MODES:
        raise ValueError(
            f"Invalid seeding_mode {seeding_mode!r}. "
            f"Allowed modes: {sorted(SEEDING_MODES)}"
        )
    if seed_match_mode not in SEED_MATCH_MODES:
        raise ValueError(
            f"Invalid seed_match_mode {seed_match_mode!r}. "
            f"Allowed modes: {sorted(SEED_MATCH_MODES)}"
        )

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    seeds: set[str] = set()
    if seeding_mode == "entity_only":
        if seed_entities:
            seeds |= resolve_seed_entities(
                graph, seed_entities, aliases=seed_aliases, seed_match_mode=seed_match_mode
            )
    elif seeding_mode == "query_only":
        if query:
            seeds |= _seeds_from_query(edges, query)
    else:  # "hybrid" -- existing behavior, both sources combined.
        if seed_entities:
            seeds |= resolve_seed_entities(
                graph, seed_entities, aliases=seed_aliases, seed_match_mode=seed_match_mode
            )
        if query:
            seeds |= _seeds_from_query(edges, query)

    if not seeds:
        return {"nodes": {}, "edges": []}

    visited_nodes: set[str] = set(seeds)
    visited_edge_ids: set[int] = set()
    result_edges: list[dict] = []
    frontier: set[str] = set(seeds)

    for _ in range(max(int(depth), 0)):
        next_frontier: set[str] = set()
        for edge in edges:
            if edge["source"] in frontier or edge["target"] in frontier:
                edge_id = id(edge)
                if edge_id not in visited_edge_ids:
                    visited_edge_ids.add(edge_id)
                    result_edges.append(edge)
                next_frontier.add(edge["source"])
                next_frontier.add(edge["target"])
        new_nodes = next_frontier - visited_nodes
        visited_nodes |= next_frontier
        if not new_nodes:
            break
        frontier = new_nodes

    result_nodes = {node_id: nodes[node_id] for node_id in visited_nodes if node_id in nodes}
    return {"nodes": result_nodes, "edges": result_edges}
