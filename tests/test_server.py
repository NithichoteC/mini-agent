"""Offline tests for server.py (the web UI's back end): scripted model, no key, no network.
Skipped when the optional UI dependencies (requirements-ui.txt) are not installed.

    python -m unittest tests.test_server -v
"""
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import loop
from tests.test_agent import action, scripted

try:
    from fastapi.testclient import TestClient
    import server
except ImportError:                     # fastapi / httpx / ruamel.yaml missing: nothing to test here
    server = None

ROOT = Path(__file__).resolve().parent.parent


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@unittest.skipIf(server is None, "web UI dependencies not installed (pip install -r requirements-ui.txt)")
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        runtime = self.tmp / "runtime.yaml"
        shutil.copyfile(ROOT / "config" / "runtime.yaml", runtime)
        text = (ROOT / "config" / "workflow.yaml").read_text()
        text = (text.replace("dir: sandbox", f"dir: {self.tmp}")
                    .replace("path: sandbox/trace.db", f"path: {self.tmp / 'trace.db'}")
                    .replace("runtime: config/runtime.yaml", f"runtime: {runtime}"))
        self.config = self.tmp / "workflow.yaml"
        self.config.write_text(text)
        self.runtime = runtime
        self._call_llm = loop.call_llm
        self.app = server.create_app(str(self.config))
        self.client = TestClient(self.app)

    def tearDown(self):
        loop.call_llm = self._call_llm
        shutil.rmtree(self.tmp)

    def chat(self, replies, **body):
        loop.call_llm = scripted(replies)
        r = self.client.post("/api/chat", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return events(r)

    def answer_when_asked(self, decision, reason=""):
        """The browser's side of an approval: wait for the question, then POST the answer."""
        def run():
            for _ in range(200):
                if self.app.state.approvals:
                    aid = next(iter(self.app.state.approvals))
                    self.client.post(f"/api/approvals/{aid}", json={"decision": decision, "reason": reason})
                    return
                time.sleep(0.02)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_a_turn_streams_steps_and_ends_with_the_answer(self):
        evs = self.chat([action("write", path="a.txt", content="hi"), action("final_answer", answer="wrote it"),
                         "VERDICT: PASS"], message="write a.txt")
        kinds = [e["type"] for e in evs]
        self.assertEqual((kinds[0], kinds[1], kinds[-1]), ("turn", "session.start", "session.end"))
        tool = [p for e in evs if e["type"] == "step" for p in e["parts"] if p["type"] == "tool"][0]
        self.assertEqual((tool["tool"], tool["title"], tool["state"]["status"]), ("write", "Write a.txt", "completed"))
        self.assertIn({"type": "review", "step": "review", "model": None, "text": "VERDICT: PASS"},
                      [p for e in evs if e["type"] == "step" for p in e["parts"]])
        end = evs[-1]
        self.assertEqual((end["status"], end["answer"]), ("done", "wrote it"))

    def test_sessions_are_listed_and_a_session_continues(self):
        first = self.chat([action("final_answer", answer="4"), "VERDICT: PASS"], message="2+2?")
        sid = first[1]["session"]
        listed = self.client.get("/api/sessions").json()
        self.assertEqual([(s["id"], s["title"], s["status"]) for s in listed], [(sid, "2+2?", "done")])
        self.chat([action("final_answer", answer="6"), "VERDICT: PASS"], message="and 3+3?", session=sid)
        doc = self.client.get(f"/api/sessions/{sid}").json()
        users = [m["text"] for m in doc["messages"] if m["info"]["role"] == "user"]
        answers = [p["text"] for m in doc["messages"] if m["info"]["role"] == "assistant"
                   for p in m["parts"] if p["type"] == "text"]
        self.assertEqual((users, answers[-2:]), (["2+2?", "and 3+3?"], ["4", "6"]))
        self.assertEqual(len(self.client.get("/api/sessions").json()), 1)       # same session, not a new one
        steps = self.client.get(f"/api/sessions/{sid}/trace").json()["steps"]
        self.assertEqual([s["step_id"] for s in steps].count("user"), 2)
        download = self.client.get(f"/api/sessions/{sid}/transcript")
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertIn("resume", download.json())

    def test_unknown_session_is_a_clear_404(self):
        r = self.client.get("/api/sessions/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("no session matches 'nope'", r.json()["detail"])

    def test_approval_allow_runs_the_tool(self):
        t = self.answer_when_asked("allow")
        evs = self.chat([action("bash", command="echo hello"), action("final_answer", answer="said hello"),
                         "VERDICT: PASS"], message="say hello")
        t.join()
        ask = [e for e in evs if e["type"] == "approval"][0]
        self.assertEqual((ask["tool"], ask["title"], ask["always"]), ("bash", "Run echo hello", "echo"))
        tool = [p for e in evs if e["type"] == "step" for p in e["parts"] if p["type"] == "tool"][0]
        self.assertEqual(tool["state"]["decision"], "ask_yes")
        self.assertIn("hello", tool["state"]["output"])

    def test_approval_deny_sends_the_reason_back_to_the_agent(self):
        t = self.answer_when_asked("deny", "not today")
        evs = self.chat([action("bash", command="rm x"), action("final_answer", answer="I was not allowed"),
                         "VERDICT: BLOCKED"], message="remove x")
        t.join()
        tool = [p for e in evs if e["type"] == "step" for p in e["parts"] if p["type"] == "tool"][0]
        self.assertEqual((tool["state"]["decision"], tool["state"]["status"]), ("ask_no", "denied"))
        self.assertEqual(tool["state"]["output"], "the user declined to run bash: not today")
        self.assertEqual(self.client.post("/api/approvals/gone", json={"decision": "allow"}).status_code, 404)

    def test_upload_is_attached_to_the_message(self):
        up = self.client.post("/api/uploads", files={"file": ("notes.txt", b"the code word is pelican", "text/plain")})
        self.assertEqual(up.status_code, 200, up.text)
        self.assertEqual((up.json()["name"], up.json()["size"]), ("notes.txt", 24))
        seen = []
        replies = iter([action("final_answer", answer="pelican"), "VERDICT: PASS"])
        loop.call_llm = lambda messages, **kw: (seen.append(messages[-1]["content"]),
                                                {"ok": True, "text": next(replies), "usage": {"total_tokens": 10}})[1]
        evs = events(self.client.post("/api/chat", json={"message": "what is the code word?", "files": [up.json()["id"]]}))
        self.assertIn("the code word is pelican", seen[0])                     # the note went to the model
        doc = self.client.get(f"/api/sessions/{evs[1]['session']}").json()
        user = doc["messages"][0]
        self.assertEqual(user["text"], "what is the code word?")
        self.assertEqual(user["attachments"][0]["name"], "notes.txt")
        self.assertTrue(user["attachments"][0]["note"].startswith("Attached file notes.txt"))
        self.assertEqual(self.client.post("/api/chat", json={"message": "x", "files": ["nope"]}).status_code, 404)

    def test_settings_round_trip_keeps_comments(self):
        got = self.client.get("/api/settings").json()
        self.assertEqual(got["values"]["actor"], "qwen_27b")
        self.assertIn("rag_search", [t["name"] for t in got["options"]["tools"]])
        tools = [t for t in got["values"]["tools"] if t != "websearch"]
        r = self.client.put("/api/settings", json={
            "actor": "gpt_oss_120b", "actions": "tool_calls", "review": "always", "tools": tools, "rag": {"k": 6},
            "permissions": [{"tool": "bash", "pattern": "python3 *", "action": "allow"}], "loop": {"max_runs": 5}})
        self.assertEqual(r.status_code, 200, r.text)
        wf, rt = self.config.read_text(), self.runtime.read_text()
        self.assertIn("actions: tool_calls      # how the agent picks an action:", wf)
        self.assertIn("  - bash           # bash(command, timeout)", wf)              # a kept tool keeps its comment
        self.assertNotIn("- websearch", wf)
        self.assertIn("k: 6                     # passages per rag_search", wf)
        self.assertIn("review: always\n", wf)
        self.assertIn("max_runs: 5              # one action per run", wf)
        self.assertIn("{tool: bash, pattern: python3 *, action: allow}", wf)
        self.assertIn("# Which model and which endpoint", rt)
        self.assertIn("model_ref: gpt_oss_120b", rt)
        self.assertIn("    model_ref: gpt_oss_120b\n  reviewer:               # the judge", rt)
        values = self.client.get("/api/settings").json()["values"]
        self.assertEqual((values["actor"], values["rag"]["k"], values["loop"]["max_runs"]), ("gpt_oss_120b", 6, 5))
        self.assertEqual(values["permissions"], [{"tool": "bash", "pattern": "python3 *", "action": "allow"}])

    def test_settings_reject_invalid_values_and_write_nothing(self):
        before = self.config.read_text(), self.runtime.read_text()
        for body, words in [({"actor": "gpt-9"}, "not a model in runtime.yaml"),
                            ({"tools": ["bash", "teleport"]}, "not in the registry: teleport"),
                            ({"permissions": [{"tool": "bash", "action": "maybe"}]}, "invalid action"),
                            ({"rag": {"type": "magic"}}, "rag type must be one of"),
                            ({"loop": {"max_runs": 0}}, "max_runs must be a whole number from 1 to 100"),
                            ({"actions": "telepathy"}, "actions must be one of"),
                            ({"review": "sometimes"}, "review must be one of")]:
            r = self.client.put("/api/settings", json=body)
            self.assertEqual(r.status_code, 422, body)
            self.assertIn(words, r.json()["detail"])
        self.assertEqual((self.config.read_text(), self.runtime.read_text()), before)

    def test_meters_from_the_trace(self):
        evs = self.chat([action("final_answer", answer="4"), "VERDICT: PASS"], message="2+2?")
        m = self.client.get("/api/meters", params={"session": evs[1]["session"]}).json()
        self.assertEqual(m["context"]["used"], 10)
        self.assertEqual(m["context"]["window"], None)              # the scripted model is not in runtime.yaml
        qwen = [d for d in m["daily"] if d["model"] == "qwen/qwen3.8-27b"][0]
        self.assertEqual((qwen["cap"], qwen["used"]), (200000, 0))
        self.assertIn("recorded in the trace", m["note"])

    def test_meters_compute_percent_for_a_known_model(self):
        replies = iter([action("final_answer", answer="4"), "VERDICT: PASS"])
        loop.call_llm = lambda messages, **kw: {"ok": True, "text": next(replies), "model": "qwen/qwen3.8-27b",
                                                "usage": {"total_tokens": 3500}}
        evs = events(self.client.post("/api/chat", json={"message": "2+2?"}))
        m = self.client.get("/api/meters", params={"session": evs[1]["session"]}).json()
        # the window is the plan's per-request limit (7,000), not the model's 131,072
        self.assertEqual((m["context"]["window"], m["context"]["context_window"], m["context"]["percent"]),
                         (7000, 131072, 50.0))
        qwen = [d for d in m["daily"] if d["model"] == "qwen/qwen3.8-27b"][0]
        self.assertEqual((qwen["used"], qwen["percent"]), (3500, 1.8))   # review: auto - no reviewer call

    def test_stop_ends_a_waiting_turn(self):
        loop.call_llm = scripted([action("bash", command="sleep 1"), action("final_answer", answer="never")])
        def stopper():
            for _ in range(200):
                if self.app.state.approvals:
                    tid = next(iter(self.app.state.turns))
                    self.assertEqual(self.client.post(f"/api/turns/{tid}/stop").json(), {"ok": True})
                    return
                time.sleep(0.02)
        t = threading.Thread(target=stopper)
        t.start()
        evs = events(self.client.post("/api/chat", json={"message": "run something"}))
        t.join()
        end = [e for e in evs if e["type"] == "session.end"][0]
        self.assertEqual(end["status"], "stopped")
        self.assertEqual(self.client.post("/api/turns/nope/stop").status_code, 404)


if __name__ == "__main__":
    unittest.main()
