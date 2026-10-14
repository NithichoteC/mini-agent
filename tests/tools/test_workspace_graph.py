"""The workspace knowledge graph: extraction only of unread sections, links, backlinks, the UI view, context."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

from agentic_ai.retrieval import rag
from agentic_ai.retrieval.workspace_graph import WorkspaceGraph, stem
from agentic_ai.retrieval.workspace_index import WorkspaceIndex

NOTES = {
    "projects/radio.md": "# Radio contract\nThe radio contract is owned by Somchai.\n\nIt depends on [[budget]].",
    "projects/budget.md": "# Budget\nThe 2569 budget funds the radio contract.",
    "journal/monday.md": "# Monday\nMet Somchai about [[Radio]] and [[projects/budget|money]].",
}


class FakeExtractor:
    """Answers every batch with one triple per labelled chunk; counts calls and the chunks it saw."""

    def __init__(self, fail_on: int | None = None):
        self.calls, self.seen, self.fail_on = 0, [], fail_on

    def __call__(self, prompt):
        self.calls += 1
        if self.fail_on == self.calls:
            return {"ok": False, "error": {"code": "rate_limited", "message": "daily limit reached, retry tomorrow"}}
        ids = [line[len("[chunk "):-1] for line in prompt.splitlines() if line.startswith("[chunk ")]
        self.seen += ids
        paths = [{"source": "Somchai", "relation": "owns", "target": f"thing {i}", "evidence": "e",
                  "source_chunk_id": i} for i in ids]
        return {"ok": True, "text": json.dumps(paths), "usage": {"total_tokens": 100}}


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class WorkspaceGraphTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for rel, text in NOTES.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        self.index = WorkspaceIndex(self.root, rag.HashEmbedder())
        self.index.scan()
        self.graph = WorkspaceGraph(self.index)

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.root)

    def test_links_resolve_by_name_path_and_label(self):
        links = {(link["source"], link["target"]) for link in self.graph.links()}
        self.assertEqual(links, {("projects/radio.md", "projects/budget.md"),
                                 ("journal/monday.md", "projects/radio.md"),
                                 ("journal/monday.md", "projects/budget.md")})
        back = self.graph.backlinks("projects/budget.md")
        self.assertEqual(sorted(b["path"] for b in back), ["journal/monday.md", "projects/radio.md"])
        self.assertIn("[[budget]]", next(b["text"] for b in back if b["path"] == "projects/radio.md"))
        self.assertEqual(stem("a/Plan v1.2.md"), "plan v1.2")

    def test_build_reads_each_section_once(self):
        llm = FakeExtractor()
        stats = self.graph.build(llm=llm)
        self.assertEqual((stats["done"], stats["total"], stats["failed"]), (3, 3, 0))
        self.assertEqual(llm.calls, 1)                            # three short sections share one call
        self.assertEqual(self.graph.build(llm=llm)["total"], 0)  # nothing new: no call
        self.assertEqual(llm.calls, 1)

    def test_a_move_costs_nothing_and_an_edit_is_read_again(self):
        llm = FakeExtractor()
        self.graph.build(llm=llm)
        (self.root / "archive").mkdir()
        (self.root / "journal/monday.md").rename(self.root / "archive/monday.md")
        self.index.move("journal/monday.md", "archive/monday.md")
        self.assertEqual(self.graph.pending(), [])
        (self.root / "projects/budget.md").write_text("# Budget\nThe 2570 budget is smaller.")
        self.index.index_file("projects/budget.md")
        self.assertEqual([p["path"] for p in self.graph.pending()], ["projects/budget.md"])

    def test_a_failed_call_stops_and_leaves_the_rest_pending(self):
        llm = FakeExtractor(fail_on=2)
        stats = self.graph.build(llm=llm, batch_chars=60)        # one section per call
        self.assertEqual(stats["done"], 1)
        self.assertIn("daily limit", stats["stopped"])
        self.assertEqual(len(self.graph.pending()), 2)

    def test_budget_and_stop_are_honoured(self):
        self.assertIn("budget", self.graph.build(llm=FakeExtractor(), batch_chars=60, budget_tokens=150)["stopped"])
        self.assertEqual(self.graph.build(llm=FakeExtractor(), should_stop=lambda: True)["stopped"],
                         "stopped by the user")

    def test_folder_scope_for_pending_view_and_paths(self):
        self.graph.build("projects", llm=FakeExtractor())
        self.assertEqual(self.graph.stats("projects")["pending"], 0)
        self.assertEqual(self.graph.stats()["pending"], 1)
        view = self.graph.view("projects")
        kinds = {n["kind"] for n in view["nodes"]}
        self.assertEqual(kinds, {"note", "entity"})
        self.assertEqual(view["stats"]["notes"], 2)
        self.assertTrue(all(p["path"].startswith("projects/") for p in self.graph.paths("projects")))
        rel = next(e for e in view["edges"] if e["kind"] == "relation")
        self.assertEqual((rel["label"], rel["source"]), ("owns", "entity:Somchai"))

    def test_context_puts_edges_from_the_hit_sections_first(self):
        self.graph.build(llm=FakeExtractor())
        hits = self.index.search("2569 budget", k=1, type="bm25")
        edges = self.graph.context("what does the budget fund", hits=hits, max_edges=2)
        self.assertEqual(edges[0]["path"], "projects/budget.md")
        self.assertEqual(edges[0]["source"], "Somchai")
        self.assertLessEqual(len(edges), 2)
        self.assertEqual(self.graph.context("anything", folder="nowhere-empty"), [])


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class GraphViewDuplicateTests(unittest.TestCase):
    def test_the_view_has_no_duplicate_edges(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "a.md").write_text("# A\nSee [[b]] and again [[b]].\n\n## Fact\nX owns Y.")
            (root / "b.md").write_text("# B\nX owns Y.")             # the same fact in another note
            index = WorkspaceIndex(root, rag.HashEmbedder())
            index.scan()
            graph = WorkspaceGraph(index)
            reply = lambda prompt: {"ok": True, "usage": {}, "text": json.dumps(
                [{"source": "X", "relation": "owns", "target": "Y", "evidence": "X owns Y.", "source_chunk_id": i}
                 for i in (line[7:-1] for line in prompt.splitlines() if line.startswith("[chunk "))])}
            graph.build(llm=reply)
            edges = graph.view()["edges"]
            keys = [(e["source"], e["target"], e["kind"], e.get("label")) for e in edges]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(sum(1 for k in keys if k[2] == "link"), 1)
            self.assertEqual(sum(1 for k in keys if k[2] == "relation"), 1)
            index.close()
        finally:
            shutil.rmtree(root)
