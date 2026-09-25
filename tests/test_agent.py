"""Offline tests: the model is replaced by a scripted list of replies.

    python -m unittest -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import llm_handler
import loop
import main
import registry
import sandbox
import tools
import tracedb

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
        ok = sandbox.run("echo hi", ws)
        err = sandbox.run("echo boom >&2; exit 3", ws)
        self.assertEqual((ok["stdout"], ok["exit_code"]), ("hi\n", 0))
        self.assertEqual(err["exit_code"], 3)
        self.assertIn("boom", err["stderr"])

    def test_timeout_kills_the_command(self):
        ws = sandbox.new_workspace(self.tmp)
        r = sandbox.run("sleep 5", ws, timeout_sec=1)
        self.assertTrue(r["timed_out"])
        self.assertEqual(r["exit_code"], -1)

    def test_output_is_truncated(self):
        ws = sandbox.new_workspace(self.tmp)
        r = sandbox.run("python3 -c \"print('x' * 10000)\"", ws, max_output_chars=100)
        self.assertLess(len(r["stdout"]), 200)
        self.assertIn("truncated", r["stdout"])

    def test_the_command_runs_inside_the_workspace(self):
        ws = sandbox.new_workspace(self.tmp)
        (ws / "marker.txt").write_text("here")
        self.assertIn("marker.txt", sandbox.run("ls", ws)["stdout"])

    def test_secrets_are_kept_out_of_the_child_environment(self):
        ws = sandbox.new_workspace(self.tmp)
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "sk-should-not-leak", "SAFE_VAR": "fine"}):
            out = sandbox.run("env", ws)["stdout"]
        self.assertNotIn("sk-should-not-leak", out)
        self.assertIn("SAFE_VAR", out)
        self.assertNotIn("GROQ_API_KEY", sandbox.scrub_env())


class ToolTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = {"workspace": sandbox.new_workspace(self.tmp), "sandbox": self.cfg["sandbox"]}

    def test_write_then_read(self):
        tools.write(self.ctx, "a/b.txt", "hello")
        self.assertIn("hello", tools.read(self.ctx, "a/b.txt"))
        self.assertIn("a/b.txt", tools.glob(self.ctx))

    def test_read_pages_a_long_file(self):
        tools.write(self.ctx, "long.txt", "\n".join(f"line {i}" for i in range(1, 101)))
        page = tools.read(self.ctx, "long.txt", offset=10, limit=5)
        self.assertIn("   10| line 10", page)
        self.assertIn("   14| line 14", page)
        self.assertNotIn("line 15\n", page)
        self.assertIn("86 more line(s); read again with offset=15", page)

    def test_paths_outside_workspace_are_rejected(self):
        with self.assertRaises(ValueError):
            tools.read(self.ctx, "../../.env")
        with self.assertRaises(ValueError):
            tools.write(self.ctx, "/tmp/x", "no")

    def test_edit_replaces_exact_text(self):
        tools.write(self.ctx, "p.py", "a = 1\nb = 2\n")
        tools.edit(self.ctx, "p.py", "a = 1", "a = 99")
        self.assertIn("a = 99", tools.read(self.ctx, "p.py"))

    def test_edit_refuses_what_it_cannot_do_unambiguously(self):
        tools.write(self.ctx, "p.py", "x = 1\nx = 1\n")
        for old, new, kw, msg in [
            ("x = 1", "x = 1", {}, "identical"),
            ("", "y", {}, "must not be empty"),
            ("nope", "y", {}, "did not match"),
            ("x = 1", "y = 2", {}, "matched 2 times"),
        ]:
            with self.assertRaises(ValueError) as e:
                tools.edit(self.ctx, "p.py", old, new, **kw)
            self.assertIn(msg, str(e.exception))
        self.assertIn("2 replacement(s)", tools.edit(self.ctx, "p.py", "x = 1", "y = 2", replaceAll=True))

    def test_glob_and_grep(self):
        tools.write(self.ctx, "a.py", "import os\nprint('hi')\n")
        tools.write(self.ctx, "sub/b.py", "import sys\n")
        tools.write(self.ctx, "c.txt", "import nothing\n")
        self.assertIn("sub/b.py", tools.glob(self.ctx, "*.py"))
        self.assertNotIn("c.txt", tools.glob(self.ctx, "*.py"))
        hits = tools.grep(self.ctx, r"^import", include="*.py")
        self.assertIn("a.py:1: import os", hits)
        self.assertNotIn("c.txt", hits)
        self.assertIn("no matches", tools.grep(self.ctx, "zzz"))
        with self.assertRaises(ValueError):
            tools.grep(self.ctx, "(unclosed")

    def test_bash_runs_in_the_workspace(self):
        tools.write(self.ctx, "data.txt", "42")
        self.assertIn("43", tools.bash(self.ctx, "python3 -c \"print(int(open('data.txt').read()) + 1)\""))
        self.assertIn("exit 1", tools.bash(self.ctx, "exit 1"))

    def test_webfetch_refuses_non_public_targets(self):
        for url in ["ftp://example.com/x", "http://localhost:8000/", "http://127.0.0.1/", "http://169.254.169.254/"]:
            with self.assertRaises(ValueError, msg=url):
                tools.webfetch(self.ctx, url)

    def test_html_becomes_text(self):
        html = "<html><head><style>p{color:red}</style></head><body><h1>Title</h1><p>Hello  world</p>" \
               "<script>alert('x')</script></body></html>"
        text = tools.html_to_text(html)
        self.assertIn("Title", text)
        self.assertIn("Hello world", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("color:red", text)

    def test_websearch_without_a_key_says_so_instead_of_failing(self):
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            self.assertIn("TAVILY_API_KEY is not set", tools.websearch(self.ctx, "anything"))

    def test_websearch_formats_results(self):
        body = {"results": [{"title": "Primes", "url": "https://e.com/p", "content": "A prime\n  number is"}]}
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-x"}), \
             mock.patch.object(tools.requests, "post",
                               return_value=mock.Mock(json=lambda: body, raise_for_status=lambda: None)) as post:
            out = tools.websearch(self.ctx, "primes", max_results=3)
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 3)
        self.assertIn("1. Primes", out)
        self.assertIn("https://e.com/p", out)
        self.assertIn("A prime number is", out)

    def test_snapshot_is_capped_in_total(self):
        self.ctx["sandbox"]["max_output_chars"] = 200
        for i in range(50):
            tools.write(self.ctx, f"f{i:02d}.txt", "x" * 150)
        snap = tools.snapshot(self.ctx)
        self.assertLess(len(snap), 200 * 3 + 2000)
        self.assertIn("more file(s) not shown", snap)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.reg = registry.load()

    def test_registry_and_tools_py_agree(self):
        self.assertEqual(registry.audit(self.reg), [])

    def test_every_enabled_tool_exists(self):
        enabled = yaml.safe_load(CONFIG.read_text())["tools"]
        registry.check(self.reg, enabled)
        with self.assertRaises(ValueError):
            registry.check(self.reg, ["nope"])

    def test_validate_rejects_unknown_and_missing_arguments(self):
        with self.assertRaises(ValueError) as e:
            registry.validate(self.reg, "write", {"filename": "a.txt"})
        self.assertIn("unknown argument 'filename' for write; usage: write(path, content)", str(e.exception))
        with self.assertRaises(ValueError) as e:
            registry.validate(self.reg, "write", {"path": "a.txt"})
        self.assertIn("missing required argument 'content'", str(e.exception))

    def test_validate_coerces_the_types_models_actually_send(self):
        self.assertEqual(registry.validate(self.reg, "read", {"path": "a", "offset": "12"})["offset"], 12)
        self.assertIs(registry.validate(self.reg, "edit", {"path": "a", "oldString": "x",
                                                           "newString": "y", "replaceAll": "true"})["replaceAll"], True)
        self.assertEqual(registry.validate(self.reg, "read", {"path": "a", "offset": None}), {"path": "a"})
        with self.assertRaises(ValueError) as e:
            registry.validate(self.reg, "read", {"path": "a", "limit": "many"})
        self.assertIn("limit must be an integer, got 'many'", str(e.exception))

    def test_describe_and_schemas_come_from_the_json(self):
        text = registry.describe(self.reg, ["bash"])
        self.assertIn("bash(command, timeout)", text)
        self.assertIn("final_answer", text)
        by_name = {t["function"]["name"]: t["function"]["parameters"] for t in registry.schemas(self.reg, ["write", "bash"])}
        self.assertEqual(by_name["write"]["required"], ["path", "content"])
        self.assertEqual(by_name["bash"]["required"], ["command"])
        self.assertIn("final_answer", by_name)

    def test_render_gives_an_icon_and_a_human_title(self):
        self.assertEqual(registry.render(self.reg, "write", {"path": "a.html"}), ("←", "Write a.html"))
        self.assertEqual(registry.render(self.reg, "nope", {})[0], "✗")

    def test_rules_start_from_the_tool_defaults(self):
        r = registry.rules(self.reg, ["bash", "write"])
        by_tool = {rule["tool"]: rule["action"] for rule in r}
        self.assertEqual(by_tool, {"bash": "ask", "write": "allow"})

    def test_rules_rejects_an_invalid_action(self):
        with self.assertRaises(ValueError):
            registry.rules(self.reg, ["bash"], [{"tool": "bash", "pattern": "*", "action": "maybe"}])

    def test_decide_last_match_wins_and_falls_back_to_ask(self):
        rules = registry.rules(self.reg, ["bash"], [
            {"tool": "bash", "pattern": "rm *", "action": "deny"},
            {"tool": "bash", "pattern": "python *", "action": "allow"},
        ])
        self.assertEqual(registry.decide(rules, "bash", "python x.py"), "allow")
        self.assertEqual(registry.decide(rules, "bash", "rm -rf /"), "deny")
        self.assertEqual(registry.decide(rules, "bash", "echo hi"), "ask")   # falls back to bash's own default
        self.assertEqual(registry.decide(rules, "unknown_tool", "*"), "ask")

    def test_decide_overrides_can_be_replaced_by_a_later_one(self):
        rules = registry.rules(self.reg, ["bash"], [
            {"tool": "bash", "pattern": "*", "action": "deny"},
            {"tool": "bash", "pattern": "*", "action": "allow"},   # a later rule always wins
        ])
        self.assertEqual(registry.decide(rules, "bash", "anything"), "allow")

    def test_bad_registry_files_are_rejected_at_load(self):
        broken = Path(tempfile.mkdtemp()) / "tools.json"
        broken.write_text(json.dumps({"tools": {"9bad": {"description": "x", "args": {}}}}))
        with self.assertRaises(ValueError):
            registry.load(str(broken))
        broken.write_text(json.dumps({"tools": {"ok": {"description": "x", "args": {"a": {"type": "mystery"}}}}}))
        with self.assertRaises(ValueError):
            registry.load(str(broken))
        shutil.rmtree(broken.parent)


class ParseActionTests(unittest.TestCase):
    def test_fenced_json(self):
        a = loop.parse_action('thinking...\n```json\n{"tool": "x", "args": {"k": 1}}\n```')
        self.assertEqual(a, {"tool": "x", "args": {"k": 1}})

    def test_flat_json_without_args_wrapper(self):
        a = loop.parse_action('{"tool": "write", "path": "a.txt", "content": "x"}')
        self.assertEqual(a, {"tool": "write", "args": {"path": "a.txt", "content": "x"}})

    def test_bare_json_without_fence(self):
        self.assertEqual(loop.parse_action('{"tool": "x"}')["args"], {})

    def test_action_inside_prose_with_braces_around_it(self):
        text = ('We need to write css {color: red} first. {"tool": "write", "args": '
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
        r = self.run_agent(["no json", action("read", path="../../.env"), action("rm_rf")], max_runs=3)
        self.assertEqual(r["status"], "max_runs")
        self.assertIn("unknown tool", r["state"]["observation"])

    def test_wrong_argument_names_get_the_signature_back(self):
        r = self.run_agent([action("write", filename="a.txt")], max_runs=1)
        self.assertIn("usage: write(path, content)", r["state"]["observation"])

    def test_review_fail_then_pass(self):
        r = self.run_agent([
            action("write", path="index.html", content="<p>draft</p>"),
            action("final_answer", answer="done"),
            "VERDICT: FAIL\nneeds a table",
            action("write", path="index.html", content="<table></table>"),
            action("final_answer", answer="done, table added"),
            "VERDICT: PASS",
        ])
        self.assertEqual((r["status"], r["runs"]), ("done", 4))
        self.assertEqual(r["state"]["answer"], "done, table added")
        self.assertEqual((r["workspace"] / "index.html").read_text(), "<table></table>")

    def test_reviewer_runs_in_its_own_conversation(self):
        seen = []
        replies = scripted([action("final_answer", answer="x"), "VERDICT: FAIL\nno", action("glob")])
        loop.call_llm = lambda messages, **kw: (seen.append([m["role"] for m in messages]), replies(messages))[1]
        r = loop.run_workflow(self.cfg | {"loop": {"max_runs": 2, "max_repeats": 3}}, "t")
        self.assertEqual(seen[1], ["system", "user"])                       # reviewer: fresh conversation
        self.assertNotIn("VERDICT", " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant"))
        self.assertIn("VERDICT: FAIL", r["messages"][-2]["content"])       # but its verdict reaches the agent

    def test_the_reviewer_sees_what_really_ran_and_what_was_refused(self):
        self.cfg["permissions"] = [{"tool": "bash", "pattern": "rm *", "action": "deny"}]
        prompts = []
        replies = scripted([action("write", path="a.txt", content="hi"), action("bash", command="rm -rf a.txt"),
                            action("final_answer", answer="could not delete"), "VERDICT: BLOCKED\nrefused"])
        loop.call_llm = lambda messages, **kw: (prompts.append(messages[-1]["content"]), replies(messages))[1]
        loop.run_workflow(self.cfg | {"loop": {"max_runs": 3, "max_repeats": 3}}, "t")
        review_prompt = prompts[-1]
        self.assertIn("1. Write a.txt [allow] -> wrote a.txt", review_prompt)
        self.assertIn("2. Run rm -rf a.txt [deny] -> bash is blocked by policy", review_prompt)
        self.assertNotIn("final_answer [", review_prompt)          # the answer is shown on its own line

    def test_review_only_runs_after_final_answer(self):
        calls = []
        replies = scripted([action("glob"), action("glob")])
        loop.call_llm = lambda messages, **kw: (calls.append(messages[0]["content"]), replies(messages))[1]
        loop.run_workflow(self.cfg | {"loop": {"max_runs": 2, "max_repeats": 3}}, "t")
        self.assertFalse(any("reviewer" in c for c in calls))

    def test_blocked_verdict_stops_with_blocked_status(self):
        r = self.run_agent([action("final_answer", answer="no write tool"), "VERDICT: BLOCKED\ntrue"])
        self.assertEqual((r["status"], r["runs"]), ("blocked", 1))

    def test_repeated_action_stops_with_no_progress(self):
        same = action("glob")
        r = self.run_agent([same, same, same, same, same], max_runs=5)
        self.assertEqual((r["status"], r["runs"]), ("no_progress", 4))
        self.assertIn("repeated 3x", r["state"]["observation"])

    def test_ask_permission_defaults_to_deny_with_no_human_attached(self):
        # bash's permission is "ask" by default in the registry; run_workflow's default
        # confirm callback denies, so with nothing else configured this is fail-closed.
        r = self.run_agent([action("bash", command="echo 1")], max_runs=1)
        self.assertIn("declined", r["state"]["observation"])
        self.assertFalse((r["workspace"] / ".exec").exists())

    def test_ask_permission_prompts_and_a_reason_reaches_the_model(self):
        asked = []
        confirm = lambda tool, args: (asked.append(tool), ("deny", "not right now"))[1]
        r = self.run_agent([action("bash", command="echo 1")], max_runs=1, confirm=confirm)
        self.assertEqual(asked, ["bash"])
        self.assertIn("declined", r["state"]["observation"])
        self.assertIn("not right now", r["state"]["observation"])

    def test_always_is_remembered_for_the_rest_of_the_session(self):
        asked = []
        confirm = lambda tool, args: (asked.append(tool), ("always", ""))[1]
        r = self.run_agent([action("bash", command="echo 1"), action("bash", command="echo 2"),
                            action("final_answer", answer="ok"), "VERDICT: PASS"],
                           max_runs=3, confirm=confirm)
        self.assertEqual(asked, ["bash"])          # asked once; the second bash call was remembered
        self.assertEqual(r["status"], "done")

    def test_deny_rule_refuses_without_ever_asking(self):
        self.cfg["permissions"] = [{"tool": "bash", "pattern": "*", "action": "deny"}]
        asked = []
        confirm = lambda tool, args: (asked.append(tool), ("allow", ""))[1]
        r = self.run_agent([action("bash", command="echo 1")], max_runs=1, confirm=confirm)
        self.assertEqual(asked, [])
        self.assertIn("blocked by policy", r["state"]["observation"])

    def test_pattern_specific_override_wins_over_the_tool_default(self):
        self.cfg["permissions"] = [{"tool": "bash", "pattern": "echo *", "action": "allow"}]
        r = self.run_agent([action("bash", command="echo matched")], max_runs=1)
        # the default confirm callback denies; if this had still asked, the observation would
        # say "declined" instead of showing the command's real output
        self.assertIn("matched", r["state"]["observation"])

    def test_continue_session_keeps_task_tokens_and_workspace(self):
        first = self.run_agent([action("write", path="a.txt", content="draft")], max_runs=1)
        self.assertEqual(first["status"], "max_runs")
        seen = []
        replies = scripted([action("write", path="a.txt", content="final"),
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


class TraceTests(Base):
    """Every action lands in the trace with the permission decision that was made about it."""

    def setUp(self):
        super().setUp()
        self.db = tracedb.connect(":memory:")

    def test_every_action_and_the_review_verdict_are_recorded_in_order(self):
        r = self.run_agent([action("write", path="a.txt", content="hi"),
                            action("bash", command="cat a.txt"),
                            action("final_answer", answer="done"), "VERDICT: PASS\nlooks right"],
                           max_runs=3, trace_db=self.db, confirm=lambda tool, args: ("allow", ""))
        rows = tracedb.steps(self.db, r["trace_id"])
        self.assertEqual([(x["tool"], x["decision"]) for x in rows],
                         [("write", "allow"), ("bash", "ask_yes"), ("final_answer", "final_answer"), (None, "n/a")])
        self.assertEqual(rows[0]["args"], {"path": "a.txt", "content": "hi"})
        self.assertIn("hi", rows[1]["observation"])
        self.assertTrue(rows[3]["model_output"].startswith("VERDICT: PASS"))
        self.assertEqual(rows[3]["step_id"], "review")
        session = tracedb.session(self.db, r["trace_id"])
        self.assertEqual((session["status"], session["answer"]), ("done", "done"))
        self.assertTrue(r["trace_id"].endswith(r["workspace"].name))

    def test_refused_and_invalid_calls_are_recorded_too(self):
        self.cfg["permissions"] = [{"tool": "bash", "pattern": "rm *", "action": "deny"}]
        r = self.run_agent([action("bash", command="rm -rf x"), action("bash", command="ls"),
                            action("write", filename="oops"), "not json at all"],
                           max_runs=4, trace_db=self.db,
                           confirm=lambda tool, args: ("deny", "not now"))
        rows = tracedb.steps(self.db, r["trace_id"])
        self.assertEqual([x["decision"] for x in rows], ["deny", "ask_no", "invalid", "invalid"])
        self.assertEqual([x["ok"] for x in rows], [0, 0, 0, 0])
        self.assertIn("not now", rows[1]["observation"])
        self.assertEqual(rows[3]["tool"], "(unparsed)")
        usage = {(u["tool"], u["decision"]): u["calls"] for u in tracedb.tool_usage(self.db)}
        self.assertEqual(usage[("bash", "deny")], 1)
        self.assertEqual(usage[("bash", "ask_no")], 1)

    def test_a_continued_session_keeps_its_trace_id(self):
        first = self.run_agent([action("glob")], max_runs=1, trace_db=self.db)
        loop.call_llm = scripted([action("write", path="b.txt", content="x")])
        second = loop.run_workflow(self.cfg | {"loop": {"max_runs": 1, "max_repeats": 3}}, "t",
                                   previous=first, hint="go on", trace_db=self.db)
        self.assertEqual(second["trace_id"], first["trace_id"])
        self.assertEqual(len(tracedb.sessions(self.db)), 1)
        self.assertEqual([x["tool"] for x in tracedb.steps(self.db, first["trace_id"])], ["glob", "write"])

    def test_no_key_reaches_the_trace(self):
        self.cfg["permissions"] = [{"tool": "bash", "pattern": "*", "action": "allow"}]
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "sk-trace-must-not-see-this"}):
            r = self.run_agent([action("bash", command="env")], max_runs=1, trace_db=self.db)
        dump = json.dumps(tracedb.steps(self.db, r["trace_id"]))
        self.assertIn("PATH", dump)                     # the env really was captured...
        self.assertNotIn("sk-trace-must-not-see-this", dump)   # ...minus the secret

    def test_each_step_records_which_model_produced_it(self):
        replies = iter([{"ok": True, "text": action("final_answer", answer="x"), "usage": {}, "model": "actor-m"},
                        {"ok": True, "text": "VERDICT: PASS", "usage": {}, "model": "judge-m"}])
        loop.call_llm = lambda messages, **kw: next(replies)
        r = loop.run_workflow(self.cfg, "t", trace_db=self.db)
        self.assertEqual([x["model"] for x in tracedb.steps(self.db, r["trace_id"])], ["actor-m", "judge-m"])

    def test_an_older_database_gains_the_model_column(self):
        path = str(self.tmp / "old.db")
        old = __import__("sqlite3").connect(path)
        old.execute("CREATE TABLE steps (id INTEGER PRIMARY KEY, session_id TEXT, run INTEGER, step_id TEXT)")
        old.commit()
        old.close()
        cols = {r["name"] for r in tracedb.connect(path).execute("PRAGMA table_info(steps)")}
        self.assertIn("model", cols)

    def test_find_accepts_any_unique_part_of_an_id(self):
        a = tracedb.start_session(self.db, Path("x/run_001"), task="a")
        tracedb.start_session(self.db, Path("x/run_002"), task="b")
        self.assertEqual(tracedb.find(self.db, "run_001"), a)
        self.assertEqual(tracedb.find(self.db, a), a)
        with self.assertRaises(ValueError):
            tracedb.find(self.db, "run_00")        # ambiguous
        with self.assertRaises(ValueError):
            tracedb.find(self.db, "run_999")       # nothing


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
        replies = iter([self.call("write", path="a.txt", content="hi"),
                        self.call("final_answer", answer="done"),
                        {"ok": True, "text": "VERDICT: PASS", "usage": {"total_tokens": 10}, "tool_calls": [],
                         "message": {"role": "assistant", "content": "VERDICT: PASS"}}])
        loop.call_llm = lambda messages, **kw: (seen.append(kw.get("tools")), next(replies))[1]
        r = loop.run_workflow(self.cfg, "t")
        self.assertEqual(r["status"], "done")
        self.assertEqual([t["function"]["name"] for t in seen[0]][-1], "final_answer")   # declared to the API
        self.assertIsNone(seen[2])                                                       # reviewer gets no tools
        tool_msgs = [m for m in r["messages"] if m["role"] == "tool"]
        self.assertEqual(tool_msgs[0], {"role": "tool", "tool_call_id": "call_write", "content": "wrote a.txt (2 chars)"})
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
        bad = self.call("write", filename="a.txt")
        loop.call_llm = lambda messages, **kw: bad
        r = loop.run_workflow(self.cfg | {"loop": {"max_runs": 1, "max_repeats": 3}}, "t")
        tool_msgs = [m for m in r["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("usage: write(path, content)", tool_msgs[0]["content"])


class RoutingTests(unittest.TestCase):
    """config/runtime.yaml: role -> model_ref -> model -> vendor -> endpoint (the course's shape)."""

    RUNTIME = {
        "runtime": {"default_role": "actor", "request_timeout": 9},
        "vendors": {"v1": {"endpoint": "https://v1.example/chat", "key_env": "V1_KEY", "options": {"a": "vendor", "b": "vendor"}},
                    "local": {"endpoint": "http://127.0.0.1:1/chat", "requires_api_key": False},
                    "odd": {"endpoint": "https://odd.example", "endpoint_profile": "anthropic_messages"}},
        "models": {"small": {"vendor": "v1", "model": "small-1", "options": {"b": "model", "c": "model"}},
                   "mine": {"vendor": "local", "model": "llama3"},
                   "weird": {"vendor": "odd", "model": "x"},
                   "ghost": {"vendor": "nowhere", "model": "y"}},
        "roles": {"actor": {"model_ref": "small"},
                  "judge": {"model_ref": "small", "options": {"c": "role", "temperature": 0.0}},
                  "broken": {"model_ref": "missing"}},
    }

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = str(self.dir / "runtime.yaml")
        Path(self.path).write_text(yaml.safe_dump(self.RUNTIME))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def resolve(self, **kw):
        return llm_handler.resolve(config_path=self.path, **kw)

    def test_role_resolves_through_model_ref_to_a_vendor_endpoint(self):
        t = self.resolve(role="actor")
        self.assertEqual((t["vendor"], t["model"], t["endpoint"], t["timeout"]), ("v1", "small-1", "https://v1.example/chat", 9))
        self.assertEqual(self.resolve()["model"], "small-1")                       # default_role

    def test_options_layer_vendor_then_model_then_role(self):
        self.assertEqual(self.resolve(role="judge")["options"], {"a": "vendor", "b": "model", "c": "role", "temperature": 0.0})

    def test_an_explicit_model_wins_as_a_key_or_as_a_raw_id(self):
        self.assertEqual(self.resolve(model="mine", role="actor")["model"], "llama3")
        raw = self.resolve(model="brand-new-model", role="actor")
        self.assertEqual((raw["vendor"], raw["model"]), ("v1", "brand-new-model"))   # the role's vendor

    def test_bad_config_becomes_an_error_dict_not_an_exception(self):
        for kw, code in [({"role": "nobody"}, "unknown_role"), ({"role": "broken"}, "unknown_model_ref"),
                         ({"model": "ghost"}, "unknown_vendor"), ({"model": "weird"}, "unsupported_profile")]:
            r = self.resolve(**kw)
            self.assertEqual((r["ok"], r["error"]["code"]), (False, code), kw)
        self.assertEqual(llm_handler.resolve(config_path=str(self.dir / "absent.yaml"))["error"]["code"], "bad_runtime_config")

    def test_call_llm_sends_to_the_resolved_endpoint_with_the_merged_options(self):
        ok = mock.Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": "hi"}}], "usage": {}})
        ok.raise_for_status = lambda: None
        with mock.patch.dict(os.environ, {"V1_KEY": "k1"}), \
             mock.patch.object(llm_handler.requests, "post", return_value=ok) as post:
            r = llm_handler.call_llm([{"role": "user", "content": "x"}], role="judge", temperature=0.7, config_path=self.path)
        url, body = post.call_args.args[0], post.call_args.kwargs["json"]
        self.assertEqual((url, body["model"], body["temperature"], body["c"]), ("https://v1.example/chat", "small-1", 0.0, "role"))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer k1")
        self.assertEqual(r["model"], "small-1")

    def test_a_vendor_without_a_key_sends_no_authorization(self):
        ok = mock.Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": "hi"}}], "usage": {}})
        ok.raise_for_status = lambda: None
        with mock.patch.object(llm_handler.requests, "post", return_value=ok) as post:
            r = llm_handler.call_llm([{"role": "user", "content": "x"}], model="mine", config_path=self.path)
        self.assertTrue(r["ok"])
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    def test_the_key_is_scrubbed_from_error_text(self):
        echo = mock.Mock(status_code=401, text="invalid api key: k-secret-123")
        echo.raise_for_status = mock.Mock(side_effect=__import__("requests").HTTPError("401 Unauthorized"))
        with mock.patch.dict(os.environ, {"V1_KEY": "k-secret-123"}), \
             mock.patch.object(llm_handler.requests, "post", return_value=echo):
            r = llm_handler.call_llm([{"role": "user", "content": "x"}], role="actor", config_path=self.path)
        self.assertEqual(r["error"]["code"], "http_error")
        self.assertNotIn("k-secret-123", r["error"]["message"])
        self.assertIn("***", r["error"]["message"])

    def test_workflow_steps_ask_for_roles_not_model_ids(self):
        cfg = yaml.safe_load(CONFIG.read_text())
        review = next(st for st in cfg["steps"] if st.get("id") == "review")
        self.assertEqual(loop.who(cfg), (None, "actor"))
        self.assertEqual(loop.who(cfg, review), (None, "reviewer"))
        self.assertEqual(loop.who(cfg, {"type": "act"}), (None, "actor"))    # no role of its own: inherit


class CliTests(Base):
    """main.py as a user runs it: the answer alone on stdout, everything else on stderr."""

    def setUp(self):
        super().setUp()
        self.cfg["trace"] = {"enabled": True, "path": str(self.tmp / "trace.db")}
        self.config = self.tmp / "workflow.yaml"
        self.config.write_text(yaml.safe_dump(self.cfg))

    def cli(self, argv, replies=()):
        import contextlib
        import io
        loop.call_llm = scripted(list(replies))
        out, err = io.StringIO(), io.StringIO()
        # stdin pinned to a non-terminal: under a real terminal the out-of-budget run would
        # otherwise stop at the hint prompt and wait for a person forever
        with mock.patch.object(sys, "argv", ["main.py", *argv]), mock.patch.object(sys, "stdin", io.StringIO("")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main.main()
                code = 0
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_run_prints_only_the_answer_on_stdout_and_exits_zero_on_pass(self):
        code, out, err = self.cli(["run", "t", "--yes", "--config", str(self.config)],
                                  [action("write", path="a.txt", content="x"), action("final_answer", answer="all done"),
                                   "VERDICT: PASS"])
        self.assertEqual((code, out), (0, "all done\n"))
        self.assertIn("Write a.txt", err)
        self.assertIn("done \u00b7 2 actions", err)

    def test_run_exits_one_when_the_task_is_not_passed(self):
        self.cfg["loop"]["max_runs"] = 1
        self.config.write_text(yaml.safe_dump(self.cfg))
        code, out, _ = self.cli(["run", "t", "--config", str(self.config)], [action("glob")])
        self.assertEqual(code, 1)
        self.assertEqual(out, "(no answer)\n")

    def test_run_format_json_streams_one_event_per_line(self):
        code, out, err = self.cli(["run", "t", "--yes", "--format", "json", "--config", str(self.config)],
                                  [action("final_answer", answer="ok"), "VERDICT: PASS"])
        events = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([e["type"] for e in events], ["session.start", "step", "step", "session.end"])
        self.assertEqual((events[-1]["status"], events[-1]["answer"]), ("done", "ok"))
        self.assertEqual(err, "")                   # nothing for humans in json mode

    def test_tools_lists_the_registry_with_effective_permissions(self):
        self.cfg["permissions"] = [{"tool": "write", "pattern": "*", "action": "deny"}]
        self.config.write_text(yaml.safe_dump(self.cfg))
        code, out, _ = self.cli(["tools", "--format", "json", "--config", str(self.config)])
        by_tool = {r["tool"]: r["permission"] for r in json.loads(out)}
        self.assertEqual((code, by_tool["bash"], by_tool["write"], by_tool["read"]), (0, "ask", "deny", "allow"))

    def test_trace_reads_back_the_run(self):
        self.cli(["run", "t", "--yes", "--config", str(self.config)],
                 [action("write", path="a.txt", content="x"), action("final_answer", answer="ok"), "VERDICT: PASS"])
        code, out, _ = self.cli(["trace", "--last", "--format", "json", "--config", str(self.config)])
        d = json.loads(out)
        self.assertEqual([x["tool"] for x in d["steps"]], ["write", "final_answer", None])
        self.assertEqual(d["session"]["status"], "done")


class HandlerTests(unittest.TestCase):
    def test_call_LLM_returns_text_or_error_dict(self):
        with mock.patch.object(llm_handler, "call_llm", return_value={"ok": True, "text": "hi", "usage": {}}):
            self.assertEqual(llm_handler.call_LLM(prompt="say hi"), "hi")
        with mock.patch.object(llm_handler, "call_llm", return_value=llm_handler._error("x", "boom", "m")):
            err = llm_handler.call_LLM(prompt="say hi", role="reviewer")
            self.assertEqual(err["ok"], False)
            self.assertEqual(set(err["error"]), {"code", "message", "provider", "model"})
        self.assertEqual(llm_handler.call_LLM(prompt="x", provider="openai")["error"]["code"], "unsupported_provider")
        self.assertEqual(llm_handler.call_LLM(prompt="x", role="nobody")["error"]["code"], "unknown_role")

    def test_missing_key_is_an_error_not_an_exception(self):
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            self.assertEqual(llm_handler.call_llm([{"role": "user", "content": "x"}])["error"]["code"], "missing_api_key")

    def test_retries_once_when_the_model_hallucinates_a_tool_call(self):
        # gpt-oss on Groq occasionally emits a tool call in its own format even though no tools
        # were declared; Groq rejects that request with this specific code. It should be retried,
        # not surfaced as a hard error.
        hallucinated = mock.Mock(status_code=400, json=lambda: {"error": {"code": "tool_use_failed"}})
        ok = mock.Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": "hi"}}], "usage": {}})
        ok.raise_for_status = lambda: None
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": "k"}), \
             mock.patch.object(llm_handler.requests, "post", side_effect=[hallucinated, ok]) as post:
            r = llm_handler.call_llm([{"role": "user", "content": "x"}])
        self.assertEqual(post.call_count, 2)
        self.assertEqual((r["ok"], r["text"]), (True, "hi"))

    def test_does_not_retry_the_hallucination_fix_when_tools_were_actually_requested(self):
        # if the caller *did* declare tools, the same error code means something else went wrong
        # with the request, not a spontaneous tool call - retrying blindly would hide that.
        hallucinated = mock.Mock(status_code=400, text='{"error": {"code": "tool_use_failed"}}',
                                 json=lambda: {"error": {"code": "tool_use_failed"}})
        hallucinated.raise_for_status = mock.Mock(side_effect=__import__("requests").HTTPError())
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": "k"}), \
             mock.patch.object(llm_handler.requests, "post", return_value=hallucinated) as post:
            r = llm_handler.call_llm([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(r["error"]["code"], "http_error")


if __name__ == "__main__":
    unittest.main()
