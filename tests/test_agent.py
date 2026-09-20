"""Offline tests: the model is replaced by a scripted list of replies.

    python -m unittest -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import llm_handler
import loop
import sandbox
import tools

CONFIG = Path(__file__).resolve().parent.parent / "config" / "workflow.yaml"


def scripted(replies):
    it = iter(replies)
    return lambda messages, **kw: {"ok": True, "text": next(it), "usage": {"total_tokens": 10}}


def action(tool, **args):
    return "```json\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = yaml.safe_load(CONFIG.read_text())
        self.cfg["sandbox"]["dir"] = str(self.tmp)
        self.cfg["llm"]["actions"] = "json_text"
        self._call_llm = loop.call_llm

    def tearDown(self):
        loop.call_llm = self._call_llm
        shutil.rmtree(self.tmp)

    def run_agent(self, replies, task="t", max_runs=None, **kw):
        loop.call_llm = scripted(replies)
        if max_runs:
            self.cfg["loop"]["max_runs"] = max_runs
        return loop.run_workflow(self.cfg, task, **kw)


class SandboxTests(Base):
    def test_run_captures_output_and_exit_code(self):
        ws = sandbox.new_workspace(self.tmp)
        ok = sandbox.run("print('hi')", ws)
        err = sandbox.run("1/0", ws)
        self.assertEqual((ok["stdout"], ok["exit_code"]), ("hi\n", 0))
        self.assertEqual(err["exit_code"], 1)
        self.assertIn("ZeroDivisionError", err["stderr"])

    def test_timeout_kills_the_script(self):
        ws = sandbox.new_workspace(self.tmp)
        r = sandbox.run("import time; time.sleep(5)", ws, timeout_sec=1)
        self.assertTrue(r["timed_out"])
        self.assertEqual(r["exit_code"], -1)

    def test_output_is_truncated(self):
        ws = sandbox.new_workspace(self.tmp)
        r = sandbox.run("print('x' * 10000)", ws, max_output_chars=100)
        self.assertLess(len(r["stdout"]), 200)
        self.assertIn("truncated", r["stdout"])


class ToolTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = {"workspace": sandbox.new_workspace(self.tmp), "sandbox": self.cfg["sandbox"]}

    def test_write_then_read(self):
        tools.write_file(self.ctx, "a/b.txt", "hello")
        self.assertEqual(tools.read_file(self.ctx, "a/b.txt"), "hello")
        self.assertIn("a/b.txt", tools.list_files(self.ctx))

    def test_paths_outside_workspace_are_rejected(self):
        with self.assertRaises(ValueError):
            tools.read_file(self.ctx, "../../.env")
        with self.assertRaises(ValueError):
            tools.write_file(self.ctx, "/tmp/x", "no")

    def test_run_python_by_path(self):
        tools.write_file(self.ctx, "hello.py", "print('from file')")
        self.assertIn("from file", tools.run_python(self.ctx, path="hello.py"))
        with self.assertRaises(ValueError):
            tools.run_python(self.ctx)

    def test_run_python_sees_workspace_files(self):
        tools.write_file(self.ctx, "data.txt", "42")
        out = tools.run_python(self.ctx, "print(int(open('data.txt').read()) + 1)")
        self.assertIn("43", out)

    def test_http_get_refuses_non_public_targets(self):
        for url in ["ftp://example.com/x", "http://localhost:8000/", "http://127.0.0.1/", "http://169.254.169.254/"]:
            with self.assertRaises(ValueError, msg=url):
                tools.http_get(self.ctx, url)

    def test_snapshot_is_capped_in_total(self):
        self.ctx["sandbox"]["max_output_chars"] = 200
        for i in range(50):
            tools.write_file(self.ctx, f"f{i:02d}.txt", "x" * 150)
        snap = tools.snapshot(self.ctx)
        self.assertLess(len(snap), 200 * 3 + 2000)
        self.assertIn("more file(s) not shown", snap)


class ParseActionTests(unittest.TestCase):
    def test_fenced_json(self):
        a = loop.parse_action('thinking...\n```json\n{"tool": "x", "args": {"k": 1}}\n```')
        self.assertEqual(a, {"tool": "x", "args": {"k": 1}})

    def test_flat_json_without_args_wrapper(self):
        a = loop.parse_action('{"tool": "write_file", "path": "a.txt", "content": "x"}')
        self.assertEqual(a, {"tool": "write_file", "args": {"path": "a.txt", "content": "x"}})

    def test_bare_json_without_fence(self):
        self.assertEqual(loop.parse_action('{"tool": "x"}')["args"], {})

    def test_action_inside_prose_with_braces_around_it(self):
        text = ('We need to write css {color: red} first. {"tool": "write_file", "args": '
                '{"path": "a.css", "content": "body {color: red}"}} then finish.')
        a = loop.parse_action(text)
        self.assertEqual(a["args"]["path"], "a.css")
        self.assertEqual(a["args"]["content"], "body {color: red}")

    def test_bad_replies_raise_with_feedback(self):
        for text in ["no action here", "```json\n{not json}\n```", '```json\n{"args": {}}\n```']:
            with self.assertRaises(ValueError):
                loop.parse_action(text)

    def test_render_leaves_json_examples_alone(self):
        out = loop.render('Task: {task}. Reply {"tool": "x"} and {unknown}', {"task": "T"})
        self.assertEqual(out, 'Task: T. Reply {"tool": "x"} and {unknown}')


class WorkflowTests(Base):
    def test_bad_actions_become_observations_not_crashes(self):
        r = self.run_agent(["no json", action("read_file", path="../../.env"), action("rm_rf")], max_runs=3)
        self.assertEqual(r["status"], "max_runs")
        self.assertIn("unknown tool", r["state"]["observation"])

    def test_wrong_argument_names_get_the_signature_back(self):
        r = self.run_agent([action("write_file", filename="a.txt")], max_runs=1)
        self.assertIn("usage: write_file(path, content)", r["state"]["observation"])

    def test_review_fail_then_pass(self):
        r = self.run_agent([
            action("write_file", path="index.html", content="<p>draft</p>"),
            action("final_answer", answer="done"),
            "VERDICT: FAIL\nneeds a table",
            action("write_file", path="index.html", content="<table></table>"),
            action("final_answer", answer="done, table added"),
            "VERDICT: PASS",
        ])
        self.assertEqual((r["status"], r["runs"]), ("done", 4))
        self.assertEqual(r["state"]["answer"], "done, table added")
        self.assertEqual((r["workspace"] / "index.html").read_text(), "<table></table>")

    def test_reviewer_runs_in_its_own_conversation(self):
        seen = []
        replies = scripted([action("final_answer", answer="x"), "VERDICT: FAIL\nno", action("list_files")])
        loop.call_llm = lambda messages, **kw: (seen.append([m["role"] for m in messages]), replies(messages))[1]
        r = loop.run_workflow(self.cfg | {"loop": {"max_runs": 2, "max_repeats": 3}}, "t")
        self.assertEqual(seen[1], ["system", "user"])                       # reviewer: fresh conversation
        self.assertNotIn("VERDICT", " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant"))
        self.assertIn("VERDICT: FAIL", r["messages"][-2]["content"])       # but its verdict reaches the agent

    def test_review_only_runs_after_final_answer(self):
        calls = []
        replies = scripted([action("list_files"), action("list_files")])
        loop.call_llm = lambda messages, **kw: (calls.append(messages[0]["content"]), replies(messages))[1]
        loop.run_workflow(self.cfg | {"loop": {"max_runs": 2, "max_repeats": 3}}, "t")
        self.assertFalse(any("reviewer" in c for c in calls))

    def test_blocked_verdict_stops_with_blocked_status(self):
        r = self.run_agent([action("final_answer", answer="no write tool"), "VERDICT: BLOCKED\ntrue"])
        self.assertEqual((r["status"], r["runs"]), ("blocked", 1))

    def test_repeated_action_stops_with_no_progress(self):
        same = action("list_files")
        r = self.run_agent([same, same, same, same, same], max_runs=5)
        self.assertEqual((r["status"], r["runs"]), ("no_progress", 4))
        self.assertIn("repeated 3x", r["state"]["observation"])

    def test_confirm_defaults_to_deny(self):
        self.cfg["confirm"] = ["run_python"]
        r = self.run_agent([action("run_python", code="print(1)")], max_runs=1)
        self.assertIn("declined", r["state"]["observation"])
        self.assertFalse((r["workspace"] / ".exec").exists())

    def test_confirm_gate_asks_and_declines(self):
        self.cfg["confirm"] = ["run_python"]
        asked = []
        r = self.run_agent([action("run_python", code="print(1)")], max_runs=1,
                           confirm=lambda tool, args: asked.append(tool) and False)
        self.assertEqual(asked, ["run_python"])
        self.assertIn("declined", r["state"]["observation"])

    def test_continue_session_keeps_task_tokens_and_workspace(self):
        first = self.run_agent([action("write_file", path="a.txt", content="draft")], max_runs=1)
        self.assertEqual(first["status"], "max_runs")
        seen = []
        replies = scripted([action("write_file", path="a.txt", content="final"),
                            action("final_answer", answer="ok"), "VERDICT: PASS"])
        loop.call_llm = lambda messages, **kw: (seen.append(messages[-1]["content"]), replies(messages))[1]
        self.cfg["loop"]["max_runs"] = 3
        second = loop.run_workflow(self.cfg, "original task", previous=first, hint="use 'final'")
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["state"]["task"], "t")                       # task not replaced by the hint
        self.assertIn("hint: use 'final'", seen[0])
        self.assertEqual(second["total_tokens"], first["total_tokens"] + 30)  # counter carried over
        self.assertEqual(second["workspace"], first["workspace"])
        self.assertEqual((second["workspace"] / "a.txt").read_text(), "final")


class NativeToolCallTests(Base):
    """llm.actions: tool_calls - the API returns structured calls instead of text."""

    def setUp(self):
        super().setUp()
        self.cfg["llm"]["actions"] = "tool_calls"

    @staticmethod
    def call(name, **args):
        return {"ok": True, "text": "", "usage": {"total_tokens": 10},
                "tool_calls": [{"id": f"call_{name}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}],
                "message": {"role": "assistant", "content": "", "tool_calls": [{"id": f"call_{name}"}]}}

    def test_tools_are_declared_and_results_go_back_as_tool_messages(self):
        seen = []
        replies = iter([self.call("write_file", path="a.txt", content="hi"),
                        self.call("final_answer", answer="done"),
                        {"ok": True, "text": "VERDICT: PASS", "usage": {"total_tokens": 10}, "tool_calls": [],
                         "message": {"role": "assistant", "content": "VERDICT: PASS"}}])
        loop.call_llm = lambda messages, **kw: (seen.append(kw.get("tools")), next(replies))[1]
        r = loop.run_workflow(self.cfg, "t")
        self.assertEqual(r["status"], "done")
        self.assertEqual([t["function"]["name"] for t in seen[0]][-1], "final_answer")   # declared to the API
        self.assertIsNone(seen[2])                                                       # reviewer gets no tools
        tool_msgs = [m for m in r["messages"] if m["role"] == "tool"]
        self.assertEqual(tool_msgs[0], {"role": "tool", "tool_call_id": "call_write_file", "content": "wrote a.txt (2 chars)"})
        self.assertEqual((r["workspace"] / "a.txt").read_text(), "hi")

    def test_plain_text_reply_counts_as_final_answer(self):
        replies = iter([{"ok": True, "text": "All done: 36 baht.", "usage": {"total_tokens": 10}, "tool_calls": [],
                         "message": {"role": "assistant", "content": "All done: 36 baht."}},
                        {"ok": True, "text": "VERDICT: PASS", "usage": {"total_tokens": 10}, "tool_calls": [],
                         "message": {"role": "assistant", "content": "VERDICT: PASS"}}])
        loop.call_llm = lambda messages, **kw: next(replies)
        r = loop.run_workflow(self.cfg, "t")
        self.assertEqual((r["status"], r["state"]["answer"]), ("done", "All done: 36 baht."))

    def test_bad_arguments_still_answer_the_tool_call(self):
        bad = self.call("write_file", filename="a.txt")
        loop.call_llm = lambda messages, **kw: bad
        r = loop.run_workflow(self.cfg | {"loop": {"max_runs": 1, "max_repeats": 3}}, "t")
        tool_msgs = [m for m in r["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("usage: write_file(path, content)", tool_msgs[0]["content"])

    def test_schemas_mark_optional_arguments(self):
        by_name = {t["function"]["name"]: t["function"]["parameters"] for t in tools.schemas(["write_file", "run_python"])}
        self.assertEqual(by_name["write_file"]["required"], ["path", "content"])
        self.assertEqual(by_name["run_python"]["required"], [])
        self.assertIn("final_answer", by_name)


class HandlerTests(unittest.TestCase):
    def test_call_LLM_returns_text_or_error_dict(self):
        with mock.patch.object(llm_handler, "call_llm", return_value={"ok": True, "text": "hi", "usage": {}}):
            self.assertEqual(llm_handler.call_LLM(prompt="say hi"), "hi")
        with mock.patch.object(llm_handler, "call_llm", return_value=llm_handler._error("x", "boom", "m")):
            err = llm_handler.call_LLM(prompt="say hi", role="reviewer")
            self.assertEqual(err["ok"], False)
            self.assertEqual(set(err["error"]), {"code", "message", "provider", "model"})
        self.assertEqual(llm_handler.call_LLM(prompt="x", provider="openai")["error"]["code"], "unsupported_provider")

    def test_missing_key_is_an_error_not_an_exception(self):
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            self.assertEqual(llm_handler.call_llm([{"role": "user", "content": "x"}])["error"]["code"], "missing_api_key")


if __name__ == "__main__":
    unittest.main()
