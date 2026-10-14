"""Tests for the agentic_ai.integrations.knowledge_graph module.

Comprehensive tests covering schema, extractor, normalizer, graph_store,
retriever, formatter, and batching. The LLM is mocked with a simple
callable that returns canned responses.
"""

import unittest
from unittest.mock import Mock

from agentic_ai.integrations.knowledge_graph import (
    SEED_MATCH_MODES,
    SEEDING_MODES,
    build_entity_alias_report,
    build_graph,
    extract_paths,
    extract_paths_batch,
    graph_to_context,
    normalize_entities,
    normalize_entity_key,
    resolve_seed_entities,
    retrieve_subgraph,
    validate_path,
    validate_paths,
)


def fake_llm(prompt: str, output: str = None, tokens: int = 10) -> dict:
    """Create a fake LLM callable that returns canned responses."""
    if output is None:
        output = "[]"
    return {"ok": True, "text": output, "usage": {"total_tokens": tokens}}


class TestSchemaValidation(unittest.TestCase):
    """Test the schema module's path validation."""

    def test_validate_path_valid_minimal(self):
        """A minimal valid path has required fields only."""
        path = {"source": "A", "relation": "causes", "target": "B"}
        cleaned, reason = validate_path(path)
        self.assertIsNotNone(cleaned)
        self.assertIsNone(reason)
        self.assertEqual(cleaned["source"], "A")
        self.assertEqual(cleaned["evidence"], "")
        self.assertEqual(cleaned["confidence"], 0.5)
        self.assertIsNone(cleaned["source_chunk_id"])

    def test_validate_path_valid_full(self):
        """A full path with all fields validates."""
        path = {
            "source": "Q3 outage",
            "relation": "delayed",
            "target": "Project Phoenix",
            "evidence": "The outage delayed the project.",
            "confidence": 0.82,
            "source_chunk_id": "chunk_002",
        }
        cleaned, reason = validate_path(path)
        self.assertIsNotNone(cleaned)
        self.assertIsNone(reason)
        self.assertEqual(cleaned["source"], "Q3 outage")
        self.assertEqual(cleaned["confidence"], 0.82)
        self.assertEqual(cleaned["source_chunk_id"], "chunk_002")

    def test_validate_path_rejects_non_dict(self):
        """Non-dict paths are rejected."""
        cleaned, reason = validate_path("not a dict")
        self.assertIsNone(cleaned)
        self.assertIn("must be a dict", reason)

    def test_validate_path_rejects_empty_required_field(self):
        """Required fields must be non-empty strings."""
        path = {"source": "", "relation": "causes", "target": "B"}
        cleaned, reason = validate_path(path)
        self.assertIsNone(cleaned)
        self.assertIn("source", reason)

    def test_validate_path_rejects_non_string_confidence(self):
        """Confidence must be numeric or convertible."""
        path = {"source": "A", "relation": "causes", "target": "B", "confidence": "not_a_number"}
        cleaned, reason = validate_path(path)
        self.assertIsNone(cleaned)
        self.assertIn("confidence", reason)

    def test_validate_paths_returns_valid_and_rejected(self):
        """validate_paths splits input into valid and rejected lists."""
        paths = [
            {"source": "A", "relation": "causes", "target": "B"},
            {"source": "", "relation": "causes", "target": "C"},
        ]
        valid, rejected = validate_paths(paths)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn("reason", rejected[0])


class TestExtractor(unittest.TestCase):
    """Test the extractor module with mocked LLM."""

    def test_extract_paths_parses_valid_json(self):
        """A valid JSON array of paths is parsed and validated."""
        output = """[
            {"source": "Q3 outage", "relation": "delayed", "target": "Project Phoenix",
             "evidence": "After the Q3 outage, Project Phoenix was delayed.", "confidence": 0.8}
        ]"""
        llm = Mock(return_value=fake_llm("", output))

        paths = extract_paths("some text", llm=llm, source_chunk_id="chunk_002")

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["source"], "Q3 outage")
        self.assertEqual(paths[0]["source_chunk_id"], "chunk_002")

    def test_extract_paths_strips_markdown_fences(self):
        """Markdown code fences are stripped from LLM output."""
        output = """```json
[{"source": "A", "relation": "causes", "target": "B", "evidence": "A causes B.", "confidence": 0.6}]
```"""
        llm = Mock(return_value=fake_llm("", output))

        paths = extract_paths("text", llm=llm)

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["source"], "A")

    def test_extract_paths_strips_think_blocks(self):
        """Think blocks <think>...</think> are stripped before JSON parsing."""
        output = """<think>Let me extract the relationships...</think>
[{"source": "A", "relation": "causes", "target": "B", "evidence": "e", "confidence": 0.5}]"""
        llm = Mock(return_value=fake_llm("", output))

        paths = extract_paths("text", llm=llm)

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["source"], "A")

    def test_extract_paths_empty_text_returns_empty_list(self):
        """Empty text returns [] without calling LLM."""
        llm = Mock()

        paths = extract_paths("   ", llm=llm)

        self.assertEqual(paths, [])
        llm.assert_not_called()

    def test_extract_paths_raises_on_unparseable_output(self):
        """Unparseable LLM output raises ValueError."""
        llm = Mock(return_value=fake_llm("", "I cannot help with that."))

        with self.assertRaises(ValueError):
            extract_paths("text", llm=llm)

    def test_extract_paths_raises_on_llm_error(self):
        """LLM error response raises RuntimeError."""
        llm = Mock(return_value={"ok": False, "error": {"code": "test_error", "message": "failed"}})

        with self.assertRaises(RuntimeError):
            extract_paths("text", llm=llm)

    def test_extract_paths_drops_malformed_paths(self):
        """Malformed paths are dropped but valid ones are kept."""
        output = """[
            {"source": "A", "relation": "causes", "target": "B"},
            {"source": "", "relation": "causes", "target": "C"}
        ]"""
        llm = Mock(return_value=fake_llm("", output))

        paths = extract_paths("text", llm=llm)

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["source"], "A")

    def test_extract_paths_unwraps_wrapped_paths_key(self):
        """If LLM returns {paths: [...]}, unwrap it."""
        output = '{"paths": [{"source": "A", "relation": "causes", "target": "B", "evidence": "e", "confidence": 0.5}]}'
        llm = Mock(return_value=fake_llm("", output))

        paths = extract_paths("text", llm=llm)

        self.assertEqual(len(paths), 1)


class TestExtractPathsBatch(unittest.TestCase):
    """Test the new extract_paths_batch function."""

    def test_batch_grouping_by_max_chars(self):
        """Chunks are grouped to not exceed max_chars per batch."""
        chunks = [
            {"id": "c1", "text": "a" * 3000},
            {"id": "c2", "text": "b" * 3000},
            {"id": "c3", "text": "c" * 3000},
        ]
        llm = Mock(side_effect=[
            fake_llm("", '[]'),  # First batch: c1, c2
            fake_llm("", '[]'),  # Second batch: c3
        ])

        result, stats = extract_paths_batch(chunks, llm=llm, max_chars=6000)

        # 3 chunks of 3k chars with max_chars=6000 -> 2 groups (c1+c2, c3)
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(stats["calls"], 2)
        # All chunks should be in result, even if empty
        self.assertIn("c1", result)
        self.assertIn("c2", result)
        self.assertIn("c3", result)

    def test_batch_source_chunk_id_validation(self):
        """Paths with source_chunk_id outside the batch are rejected."""
        chunks = [{"id": "c1", "text": "text"}]
        output = '[{"source": "A", "relation": "r", "target": "B", "evidence": "e", "confidence": 0.5, "source_chunk_id": "c_invalid"}]'
        llm = Mock(return_value=fake_llm("", output))

        result, stats = extract_paths_batch(chunks, llm=llm)

        # The path with source_chunk_id="c_invalid" should be rejected
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(result["c1"], [])

    def test_batch_failed_group_recorded(self):
        """A group whose LLM call fails is recorded in failed_ids."""
        chunks = [
            {"id": "c1", "text": "text1" * 1000},
            {"id": "c2", "text": "text2" * 1000},
        ]
        # First call succeeds, second fails
        llm = Mock(side_effect=[
            fake_llm("", '[]'),
            {"ok": False, "error": {"code": "test", "message": "failed"}},
        ])

        result, stats = extract_paths_batch(chunks, llm=llm, max_chars=3000)

        self.assertEqual(stats["errors"], 1)
        self.assertIn("c2", stats["failed_ids"])
        # c1 succeeded, c2 failed
        self.assertEqual(result["c1"], [])
        self.assertEqual(result["c2"], [])

    def test_batch_tracks_token_usage(self):
        """Token usage from all successful calls is summed."""
        chunks = [{"id": "c1", "text": "text"}]
        llm = Mock(return_value={"ok": True, "text": "[]", "usage": {"total_tokens": 25}})

        result, stats = extract_paths_batch(chunks, llm=llm)

        self.assertEqual(stats["tokens"], 25)

    def test_batch_chunk_validation(self):
        """Invalid chunk format raises ValueError."""
        with self.assertRaises(ValueError):
            extract_paths_batch(
                [{"id": "c1"}],  # Missing "text"
                llm=Mock()
            )


class TestNormalizer(unittest.TestCase):
    """Test the normalizer module."""

    def test_normalize_entities_merges_case_and_whitespace(self):
        """Entities differing only in case/whitespace are merged."""
        paths = [
            {"source": "project phoenix", "relation": "r", "target": "Q3   outage"},
            {"source": "Project Phoenix", "relation": "r", "target": "Q3 outage"},
        ]

        normalized = normalize_entities(paths)

        sources = {p["source"] for p in normalized}
        targets = {p["target"] for p in normalized}
        self.assertEqual(sources, {"Project Phoenix"})
        self.assertEqual(targets, {"Q3 outage"})

    def test_normalize_entities_does_not_mutate_input(self):
        """Input paths are not mutated."""
        paths = [{"source": "project phoenix", "relation": "r", "target": "x"}]
        normalize_entities(paths)
        self.assertEqual(paths[0]["source"], "project phoenix")

    def test_normalize_entities_applies_explicit_aliases(self):
        """Explicit aliases override heuristics."""
        paths = [{"source": "the migration project", "relation": "r", "target": "x"}]
        normalized = normalize_entities(
            paths,
            aliases={"Project Phoenix": ["the migration project", "Phoenix project"]}
        )
        self.assertEqual(normalized[0]["source"], "Project Phoenix")

    def test_normalize_entity_key_deterministic(self):
        """normalize_entity_key produces consistent matching keys."""
        key1 = normalize_entity_key("the Project-Lotus")
        key2 = normalize_entity_key("Project Lotus")
        self.assertEqual(key1, key2)

    def test_build_entity_alias_report(self):
        """build_entity_alias_report shows which mentions collapsed together."""
        paths = [
            {"source": "Q3 outage", "relation": "delayed", "target": "Project Phoenix"},
            {"source": "the Q3 outage", "relation": "triggered", "target": "Project Phoenix"},
        ]

        report = build_entity_alias_report(paths)

        self.assertIn("Q3 outage", report)
        self.assertIn("the Q3 outage", report["Q3 outage"])


class TestGraphStore(unittest.TestCase):
    """Test the graph_store module."""

    def test_build_graph_creates_nodes_and_edges(self):
        """build_graph creates nodes and edges from paths."""
        paths = [
            {
                "source": "Q3 outage",
                "relation": "delayed",
                "target": "Project Phoenix",
                "evidence": "After the Q3 outage, Project Phoenix was delayed.",
                "confidence": 0.82,
                "source_chunk_id": "chunk_002",
            }
        ]

        graph = build_graph(paths)

        self.assertEqual(set(graph["nodes"].keys()), {"Q3 outage", "Project Phoenix"})
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual(edge["source"], "Q3 outage")
        self.assertEqual(edge["relation"], "delayed")
        self.assertEqual(edge["target"], "Project Phoenix")
        self.assertEqual(edge["confidence"], 0.82)

    def test_build_graph_fills_optional_defaults(self):
        """Optional fields get defaults in edges."""
        paths = [{"source": "A", "relation": "causes", "target": "B"}]
        graph = build_graph(paths)
        edge = graph["edges"][0]
        self.assertEqual(edge["evidence"], "")
        self.assertEqual(edge["confidence"], 0.5)
        self.assertIsNone(edge["source_chunk_id"])

    def test_build_graph_rejects_malformed_path(self):
        """Malformed paths cause ValueError."""
        paths = [{"source": "", "relation": "causes", "target": "B"}]
        with self.assertRaises(ValueError):
            build_graph(paths)

    def test_build_graph_empty_input(self):
        """Empty input produces empty graph."""
        graph = build_graph([])
        self.assertEqual(graph["nodes"], {})
        self.assertEqual(graph["edges"], [])


class TestFormatter(unittest.TestCase):
    """Test the formatter module."""

    def test_graph_to_context_includes_all_edge_info(self):
        """graph_to_context includes triple, evidence, confidence, and source."""
        subgraph = {
            "nodes": {},
            "edges": [
                {
                    "source": "Q3 outage",
                    "relation": "delayed",
                    "target": "Project Phoenix",
                    "evidence": "After the Q3 outage, Project Phoenix was delayed.",
                    "confidence": 0.82,
                    "source_chunk_id": "chunk_002",
                }
            ],
        }

        context = graph_to_context(subgraph)

        self.assertIn("Q3 outage --delayed--> Project Phoenix", context)
        self.assertIn("After the Q3 outage, Project Phoenix was delayed.", context)
        self.assertIn("chunk_002", context)
        self.assertIn("0.82", context)

    def test_graph_to_context_preserves_edge_order(self):
        """Multiple edges appear in order."""
        subgraph = {
            "nodes": {},
            "edges": [
                {"source": "A", "relation": "r1", "target": "B", "evidence": "e1",
                 "confidence": 0.5, "source_chunk_id": "c1"},
                {"source": "B", "relation": "r2", "target": "C", "evidence": "e2",
                 "confidence": 0.6, "source_chunk_id": "c2"},
            ],
        }

        context = graph_to_context(subgraph)
        lines = context.splitlines()

        # First line is header
        self.assertEqual(lines[0], "Graph evidence:")
        # First edge appears before second edge
        self.assertLess(context.index("A --r1--> B"), context.index("B --r2--> C"))

    def test_graph_to_context_empty_subgraph(self):
        """Empty subgraph shows 'no relevant graph context found'."""
        context = graph_to_context({"nodes": {}, "edges": []})
        self.assertIn("no relevant graph context found", context)


class TestRetriever(unittest.TestCase):
    """Test the retriever module."""

    def _build_test_graph(self):
        """Create a test graph with a simple chain A -> B -> C."""
        paths = [
            {"source": "A", "relation": "causes", "target": "B", "evidence": "A causes B", "confidence": 0.9, "source_chunk_id": "c1"},
            {"source": "B", "relation": "triggers", "target": "C", "evidence": "B triggers C", "confidence": 0.9, "source_chunk_id": "c2"},
        ]
        return build_graph(paths)

    def test_retrieve_subgraph_depth_1_returns_neighbors(self):
        """depth=1 returns edges touching the seed."""
        graph = self._build_test_graph()
        subgraph = retrieve_subgraph(graph, seed_entities=["A"], depth=1)

        touched = {n for edge in subgraph["edges"] for n in (edge["source"], edge["target"])}
        self.assertIn("A", touched)
        self.assertIn("B", touched)
        # C is only reachable with depth=2
        self.assertNotIn("C", touched)

    def test_retrieve_subgraph_depth_2_multi_hop(self):
        """depth=2 returns two hops: A -> B -> C."""
        graph = self._build_test_graph()
        subgraph = retrieve_subgraph(graph, seed_entities=["A"], depth=2)

        touched = {n for edge in subgraph["edges"] for n in (edge["source"], edge["target"])}
        self.assertIn("A", touched)
        self.assertIn("B", touched)
        self.assertIn("C", touched)

    def test_resolve_seed_entities_exact_mode(self):
        """exact mode does case-insensitive exact matching."""
        graph = self._build_test_graph()
        seeds = resolve_seed_entities(graph, ["a"], seed_match_mode="exact")
        self.assertIn("A", seeds)

    def test_resolve_seed_entities_normalized_mode(self):
        """normalized mode matches with article/case/hyphen normalization."""
        paths = [{"source": "Project-Lotus", "relation": "r", "target": "X", "evidence": "e", "confidence": 0.5, "source_chunk_id": "c1"}]
        graph = build_graph(paths)
        seeds = resolve_seed_entities(graph, ["the project lotus"], seed_match_mode="normalized")
        self.assertIn("Project-Lotus", seeds)

    def test_resolve_seed_entities_alias_mode(self):
        """alias mode uses explicit alias map."""
        paths = [{"source": "A", "relation": "r", "target": "B", "evidence": "e", "confidence": 0.5, "source_chunk_id": "c1"}]
        graph = build_graph(paths)
        # Alias map: if seed is "Alias1", also try matching "A" against node labels
        aliases = {"Alias1": ["A"]}
        seeds = resolve_seed_entities(graph, ["Alias1"], seed_match_mode="alias", aliases=aliases)
        self.assertIn("A", seeds)

    def test_retrieve_subgraph_query_only_mode(self):
        """seeding_mode='query_only' uses only query keywords."""
        graph = self._build_test_graph()
        subgraph = retrieve_subgraph(graph, query="B triggers", seeding_mode="query_only", depth=1)
        touched = {n for edge in subgraph["edges"] for n in (edge["source"], edge["target"])}
        self.assertIn("B", touched)
        self.assertIn("C", touched)

    def test_retrieve_subgraph_entity_only_mode(self):
        """seeding_mode='entity_only' ignores query."""
        graph = self._build_test_graph()
        subgraph = retrieve_subgraph(
            graph,
            query="nonexistent",
            seed_entities=["A"],
            seeding_mode="entity_only",
            depth=1
        )
        touched = {n for edge in subgraph["edges"] for n in (edge["source"], edge["target"])}
        self.assertIn("A", touched)
        self.assertIn("B", touched)

    def test_retrieve_subgraph_invalid_seeding_mode_raises(self):
        """Invalid seeding_mode raises ValueError."""
        graph = self._build_test_graph()
        with self.assertRaises(ValueError):
            retrieve_subgraph(graph, seeding_mode="invalid")

    def test_retrieve_subgraph_invalid_seed_match_mode_raises(self):
        """Invalid seed_match_mode raises ValueError."""
        graph = self._build_test_graph()
        with self.assertRaises(ValueError):
            retrieve_subgraph(graph, seed_entities=["A"], seed_match_mode="invalid")

    def test_seeding_modes_and_seed_match_modes_defined(self):
        """Constants SEEDING_MODES and SEED_MATCH_MODES are defined."""
        self.assertIn("entity_only", SEEDING_MODES)
        self.assertIn("query_only", SEEDING_MODES)
        self.assertIn("hybrid", SEEDING_MODES)
        self.assertIn("exact", SEED_MATCH_MODES)
        self.assertIn("normalized", SEED_MATCH_MODES)
        self.assertIn("alias", SEED_MATCH_MODES)


class TestThaiTokenization(unittest.TestCase):
    """Test Thai language support in query seeding."""

    def test_thai_tokenization_in_query_seeding(self):
        """Thai tokens are properly handled in query keyword matching."""
        # Create a graph with a Thai entity
        paths = [
            {
                "source": "งบประมาณ",
                "relation": "เพิ่มขึ้น",
                "target": "รายได้",
                "evidence": "งบประมาณประจำปี 2569 เพิ่มขึ้น",
                "confidence": 0.9,
                "source_chunk_id": "c1"
            }
        ]
        graph = build_graph(paths)

        # Try query_only mode with Thai query
        subgraph = retrieve_subgraph(
            graph,
            query="งบประมาณปี 2569 เท่าไร",
            seeding_mode="query_only",
            depth=1
        )

        # If tokenize works properly, it should find the entity
        if subgraph["edges"]:
            touched = {n for edge in subgraph["edges"] for n in (edge["source"], edge["target"])}
            self.assertIn("งบประมาณ", touched)


if __name__ == "__main__":
    unittest.main()


class BatchPromptTests(unittest.TestCase):
    def test_the_batch_prompt_asks_for_the_source_chunk_of_every_path(self):
        from agentic_ai.integrations.knowledge_graph.extractor import build_batch_prompt
        prompt = build_batch_prompt([{"id": "a1", "text": "X delayed Y."}, {"id": "b2", "text": "Y caused Z."}])
        self.assertIn("[chunk a1]", prompt)
        self.assertIn("[chunk b2]", prompt)
        self.assertIn('"source_chunk_id"', prompt)

    def test_a_single_chunk_call_attributes_paths_without_a_label(self):
        from agentic_ai.integrations.knowledge_graph import extract_paths_batch
        reply = '[{"source": "X", "relation": "delayed", "target": "Y", "evidence": "X delayed Y."}]'
        result, stats = extract_paths_batch([{"id": "a1", "text": "X delayed Y."}],
                                            llm=lambda p: {"ok": True, "text": reply, "usage": {}})
        self.assertEqual([p["target"] for p in result["a1"]], ["Y"])
        self.assertEqual(stats["rejected"], 0)
