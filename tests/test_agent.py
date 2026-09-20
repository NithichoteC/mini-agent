"""Offline tests: the model is replaced by a scripted list of replies.

    python -m unittest -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

import loop
import sandbox
import tools

CONFIG = Path(__file__).resolve().parent.parent / "config" / "workflow.yaml"


def scripted(replies):
    it = iter(replies)
    return lambda messages, **kw: {"ok": True, "text": next(it), "usage": {"total_tokens": 10}}


def action(tool, **args):
    import json
    return "```json\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = yaml.safe_load(CONFIG.read_text())
        self.cfg["sandbox"]["dir"] = str(self.tmp)
        self._call_llm = loop.call_llm

    def tearDown(self):
        loop.call_llm = self._call_llm
        shutil.rmtree(self.tmp)

    def run_agent(self, replies, task="t", max_runs=None, **kw):
        loop.call_llm = scripted(replies)
        if max_runs:
            self.cfg["loop"]["max_runs"] = max_runs
        return loop.run_workflow(self.cfg, task, log=lambda m: None, **kw)


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

    def test_run_python_sees_workspace_files(self):
        tools.write_file(self.ctx, "data.txt", "42")
        out = tools.run_python(self.ctx, "print(int(open('data.txt').read()) + 1)")
        self.assertIn("43", out)


class ParseActionTests(unittest.TestCase):
    def test_fenced_json(self):
        a = loop.parse_action('thinking...\n```json\n{"tool": "x", "args": {"k": 1}}\n```')
        self.assertEqual(a, {"tool": "x", "args": {"k": 1}})

    def test_bare_json_without_fence(self):
        self.assertEqual(loop.parse_action('{"tool": "x"}')["args"], {})

    def test_bad_replies_raise_with_feedback(self):
        for text in ["no action here", "```json\n{not json}\n```", '```json\n{"args": {}}\n```']:
            with self.assertRaises(ValueError):
                loop.parse_action(text)

    def test_render_leaves_json_examples_alone(self):
        out = loop.render('Task: {task}. Reply {"tool": "x"} and {unknown}', {"task": "T"})
        self.assertEqual(out, 'Task: T. Reply {"tool": "x"} and {unknown}')


class WorkflowTests(Base):
    def test_bad_actions_become_observations_not_crashes(self):
        r = self.run_agent([
            "no json",
            action("read_file", path="../../.env"),
            action("rm_rf"),
        ], max_runs=3)
        self.assertEqual(r["status"], "max_runs")
        self.assertIn("unknown tool", r["state"]["observation"])

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

    def test_review_only_runs_after_final_answer(self):
        calls = []
        replies = scripted([action("list_files"), action("list_files")])
        loop.call_llm = lambda messages, **kw: (calls.append(messages[-1]["content"]), replies(messages))[1]
        loop.run_workflow(self.cfg | {"loop": {"max_runs": 2}}, "t", log=lambda m: None)
        self.assertFalse(any("VERDICT" in c for c in calls))

    def test_continue_session_after_max_runs(self):
        first = self.run_agent([action("write_file", path="a.txt", content="draft")], max_runs=1)
        self.assertEqual(first["status"], "max_runs")
        second = self.run_agent([
            action("write_file", path="a.txt", content="final"),
            action("final_answer", answer="ok"),
            "VERDICT: PASS",
        ], task="Hint: finish it", max_runs=3, messages=first["messages"], workspace=first["workspace"])
        self.assertEqual(second["status"], "done")
        self.assertEqual((second["workspace"] / "a.txt").read_text(), "final")
        self.assertTrue(any("Hint" in m["content"] for m in second["messages"]))


if __name__ == "__main__":
    unittest.main()
