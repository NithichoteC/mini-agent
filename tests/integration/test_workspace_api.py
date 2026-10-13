"""The notes workspace over HTTP: tree, read/save with conflict detection, create/move/delete, search,
backlinks, the graph view and a graph build, and the chat's folder scope."""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy  # noqa: F401
    from fastapi.testclient import TestClient

    from backend import app as server
except ImportError:
    server = None

from agentic_ai.retrieval import rag
from agentic_ai.runtime import loop
from agentic_ai.tools import builtin as tools
from tests.integration.test_server import events
from tests.runtime.test_agent import action, scripted

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(server is None, "web UI or RAG dependencies not installed")
class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.notes = self.tmp / "notes"
        runtime = self.tmp / "runtime.yaml"
        shutil.copyfile(ROOT / "config" / "runtime.yaml", runtime)
        text = (ROOT / "config" / "workflow.yaml").read_text()
        text = re.sub(r"\n  - \{tool: \w+, action: ask\}[^\n]*", "", text)
        text = (text.replace("dir: sandbox", f"dir: {self.tmp / 'sandbox'}").replace("dir: workspace", f"dir: {self.notes}")
                    .replace("path: sandbox/trace.db", f"path: {self.tmp / 'trace.db'}")
                    .replace("runtime: config/runtime.yaml", f"runtime: {runtime}"))
        (self.tmp / "workflow.yaml").write_text(text)
        self.patch = mock.patch.object(tools, "_embedder", rag.HashEmbedder)
        self.patch.start()
        self._call_llm = loop.call_llm
        self.client = TestClient(server.create_app(str(self.tmp / "workflow.yaml")))

    def tearDown(self):
        loop.call_llm = self._call_llm
        self.patch.stop()
        index = tools._workspace_indexes.pop(str(self.notes.resolve()), None)
        if index:
            index.close()
        shutil.rmtree(self.tmp)

    def ok(self, response):
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def test_create_read_save_move_delete(self):
        c = self.client
        self.assertEqual(self.ok(c.post("/api/workspace/create", json={"path": "projects", "kind": "folder"})),
                         {"path": "projects"})
        made = self.ok(c.post("/api/workspace/create", json={"path": "projects/Radio plan", "kind": "file"}))
        self.assertEqual(made["path"], "projects/Radio plan.md")
        note = self.ok(c.get("/api/workspace/file", params={"path": made["path"]}))
        self.assertEqual(note["content"], "# Radio plan\n\n")
        saved = self.ok(c.put("/api/workspace/file", json={"path": made["path"], "content": "# Radio plan\n40 radios",
                                                          "base_sha": note["sha"]}))
        self.assertNotEqual(saved["sha"], note["sha"])
        tree = self.ok(c.get("/api/workspace/tree"))
        self.assertEqual(tree["children"][0]["children"][0]["path"], "projects/Radio plan.md")
        moved = self.ok(c.post("/api/workspace/move", json={"source": "projects/Radio plan.md", "destination": "radio.md"}))
        self.assertEqual(moved["path"], "radio.md")
        self.ok(c.post("/api/workspace/delete", json={"path": "projects"}))
        self.assertEqual([n["path"] for n in self.ok(c.get("/api/workspace/tree"))["children"]], ["radio.md"])
        self.assertEqual(c.post("/api/workspace/create", json={"path": "radio", "kind": "file"}).status_code, 409)

    def test_a_save_over_a_newer_version_is_a_conflict(self):
        c = self.client
        c.post("/api/workspace/create", json={"path": "n", "kind": "file"})
        opened = self.ok(c.get("/api/workspace/file", params={"path": "n.md"}))
        (self.notes / "n.md").write_text("# n\nthe agent wrote this")
        r = c.put("/api/workspace/file", json={"path": "n.md", "content": "mine", "base_sha": opened["sha"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["current"]["content"], "# n\nthe agent wrote this")

    def test_paths_outside_the_workspace_are_refused(self):
        self.assertEqual(self.client.get("/api/workspace/file", params={"path": "../workflow.yaml"}).status_code, 422)
        self.assertEqual(self.client.post("/api/workspace/delete", json={"path": ".aiblue"}).status_code, 422)

    def test_search_backlinks_and_graph(self):
        c = self.client
        c.put("/api/workspace/file", json={"path": "a/budget.md", "content": "# Budget\n2569 radios cost 4 million."})
        c.put("/api/workspace/file", json={"path": "b/plan.md", "content": "# Plan\nSee [[budget]] for radios."})
        hits = self.ok(c.get("/api/workspace/search", params={"q": "radios", "folder": "a"}))
        self.assertEqual([h["path"] for h in hits], ["a/budget.md"])
        back = self.ok(c.get("/api/workspace/backlinks", params={"path": "a/budget.md"}))
        self.assertEqual([b["path"] for b in back["links"]], ["b/plan.md"])
        view = self.ok(c.get("/api/workspace/graph"))
        self.assertEqual(view["stats"]["links"], 1)
        self.assertEqual(view["stats"]["pending"], 2)

        def extractor(prompt):
            ids = [line[7:-1] for line in prompt.splitlines() if line.startswith("[chunk ")]
            return {"ok": True, "usage": {"total_tokens": 50}, "text": json.dumps(
                [{"source": "radios", "relation": "cost", "target": "4 million", "evidence": "x",
                  "source_chunk_id": i} for i in ids])}

        with mock.patch("agentic_ai.models.llm_handler.call_llm",
                        lambda messages, **kw: extractor(messages[-1]["content"])):
            built = events(c.post("/api/workspace/graph/build", json={"folder": ""}))
        self.assertEqual(built[-1]["type"], "done")
        self.assertEqual((built[-1]["done"], built[-1]["tokens"]), (2, 50))
        view = self.ok(c.get("/api/workspace/graph"))
        self.assertEqual(view["stats"]["pending"], 0)
        self.assertIn("entity:radios", {n["id"] for n in view["nodes"]})

    def test_the_chat_scope_reaches_rag_search(self):
        c = self.client
        c.put("/api/workspace/file", json={"path": "a/x.md", "content": "# X\nradios here"})
        c.put("/api/workspace/file", json={"path": "b/y.md", "content": "# Y\nradios there"})
        loop.call_llm = scripted([action("rag_search", query="radios"), action("final_answer", answer="ok")])
        out = events(c.post("/api/chat", json={"message": "where are radios?", "scope": "b"}))
        search = next(e for e in out if e["type"] == "step" and e.get("tool") == "rag_search")
        self.assertIn("b/y.md", search["observation"])
        self.assertNotIn("a/x.md", search["observation"])
