"""The shared notes workspace: doc tools, file.changed events, records kept apart, prompt files."""
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from agentic_ai import prompts
from agentic_ai.messages import validate
from agentic_ai.runtime import loop
from agentic_ai.tools import builtin as tools
from tests.runtime.test_agent import CONFIG, action, scripted


def ctx_for(root: Path, **extra) -> dict:
    return {"workspace": root, "session": root.parent / "session", "notes": True,
            "sandbox": {"max_output_chars": 4000, "timeout_sec": 5}, **extra}


class DocToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "notes"
        self.root.mkdir()
        (self.tmp / "session").mkdir()
        self.seen = []
        self.ctx = ctx_for(self.root, on_change=self.seen.append)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_write_append_and_edit_report_each_change(self):
        tools.write(self.ctx, "a/n.md", "# N\n")
        tools.append(self.ctx, "a/n.md", "more")
        tools.edit(self.ctx, "a/n.md", "more", "less")
        self.assertEqual((self.root / "a/n.md").read_text(), "# N\nless")
        self.assertEqual([c["kind"] for c in self.seen], ["write", "write", "write"])
        self.assertEqual({c["path"] for c in self.seen}, {"a/n.md"})

    def test_append_adds_a_newline_only_when_missing(self):
        tools.append(self.ctx, "log.md", "one")
        tools.append(self.ctx, "log.md", "two\n")
        tools.append(self.ctx, "log.md", "three")
        self.assertEqual((self.root / "log.md").read_text(), "one\ntwo\nthree")

    def test_mkdir_and_list_show_the_tree(self):
        tools.mkdir(self.ctx, "projects/alpha")
        tools.write(self.ctx, "projects/alpha/plan.md", "x")
        tools.write(self.ctx, "inbox.md", "y")
        out = tools.list_dir(self.ctx, depth=3)
        self.assertIn("projects/", out)
        self.assertIn("      plan.md", out)
        self.assertIn("alpha/  (1 item(s))", tools.list_dir(self.ctx, depth=2))
        self.assertLess(out.index("projects/"), out.index("inbox.md"))      # folders first
        self.assertIn("already exists", tools.mkdir(self.ctx, "projects"))

    def test_move_renames_moves_into_a_folder_and_never_overwrites(self):
        tools.write(self.ctx, "a.md", "A")
        tools.write(self.ctx, "b.md", "B")
        tools.mkdir(self.ctx, "box")
        self.assertIn("box/a.md", tools.move(self.ctx, "a.md", "box"))
        self.assertTrue((self.root / "box/a.md").exists())
        with self.assertRaises(ValueError):
            tools.move(self.ctx, "b.md", "box/a.md")
        with self.assertRaises(ValueError):
            tools.move(self.ctx, "box", "box/inner")
        tools.move(self.ctx, "b.md", "c.md")
        self.assertEqual(self.seen[-1], {"kind": "move", "path": "b.md", "dest": "c.md"})

    def test_delete_goes_to_the_trash(self):
        tools.write(self.ctx, "old/x.md", "keep me")
        out = tools.delete(self.ctx, "old")
        self.assertFalse((self.root / "old").exists())
        trashed = list((self.root / ".aiblue" / "trash").rglob("x.md"))
        self.assertEqual([p.read_text() for p in trashed], ["keep me"])
        self.assertIn("trash", out)
        self.assertEqual(self.seen[-1]["kind"], "delete")

    def test_the_engine_folder_and_the_workspace_itself_are_off_limits(self):
        for bad in (".aiblue/index.db", ".git/config"):
            with self.assertRaises(ValueError):
                tools.write(self.ctx, bad, "x")
        with self.assertRaises(ValueError):
            tools.delete(self.ctx, ".")
        with self.assertRaises(ValueError):
            tools.read(self.ctx, "../session/x")
        tools.write(self.ctx, "n.md", "x")
        (self.root / ".aiblue").mkdir(exist_ok=True)
        (self.root / ".aiblue" / "secret.md").write_text("engine")
        self.assertNotIn(".aiblue", tools.glob(self.ctx, "*"))
        self.assertTrue(tools.grep(self.ctx, "engine").startswith("no matches"))

    def test_session_record_names_are_fine_in_a_shared_workspace(self):
        tools.write(self.ctx, "transcript.json", "{}")         # only reserved where records live
        self.assertTrue((self.root / "transcript.json").exists())

    def test_snapshot_shows_only_what_this_turn_changed(self):
        (self.root / "untouched.md").write_text("not mine")
        tools.write(self.ctx, "new.md", "fresh")
        snap = tools.snapshot(self.ctx)
        self.assertIn("new.md", snap)
        self.assertNotIn("untouched", snap)
        self.assertEqual(tools.snapshot(ctx_for(self.root)), "(no files changed in this turn)")


class NotesModeLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = yaml.safe_load(CONFIG.read_text())
        self.cfg["sandbox"]["dir"] = str(self.tmp / "sandbox")
        self.cfg["workspace"] = {"dir": str(self.tmp / "notes")}
        self.cfg["trace"]["enabled"] = False
        self.cfg["review"] = "never"
        self._call_llm = loop.call_llm

    def tearDown(self):
        loop.call_llm = self._call_llm
        shutil.rmtree(self.tmp)

    def run_agent(self, replies, **kw):
        loop.call_llm = scripted(replies)
        events = []
        result = loop.run_workflow(self.cfg, "t", emit=events.append, **kw)
        return result, events

    def test_files_go_to_the_notes_folder_and_records_to_the_session(self):
        result, events = self.run_agent([action("write", path="ideas/a.md", content="# A"),
                                         action("final_answer", answer="done")],
                                        confirm=lambda tool, args: ("allow", ""))
        self.assertEqual((self.tmp / "notes/ideas/a.md").read_text(), "# A")
        self.assertTrue(str(result["workspace"]).startswith(str(self.tmp / "sandbox")))
        self.assertEqual(result["changes"], [{"kind": "write", "path": "ideas/a.md"}])
        changed = [e for e in events if e["type"] == "file.changed"]
        self.assertEqual([(e["kind"], e["path"]) for e in changed], [("write", "ideas/a.md")])
        for e in events:
            validate(e)

    def test_writes_to_notes_ask_first(self):
        asked = []
        result, _ = self.run_agent([action("write", path="a.md", content="x"),
                                    action("final_answer", answer="could not")],
                                   confirm=lambda tool, args: (asked.append(tool), ("deny", "no"))[1])
        self.assertEqual(asked, ["write"])
        self.assertFalse((self.tmp / "notes/a.md").exists())

    def test_bash_records_stay_out_of_the_notes(self):
        self.cfg["permissions"] = [{"tool": "bash", "action": "allow"}]
        result, _ = self.run_agent([action("bash", command="echo hi > note.md"), action("final_answer", answer="ok")])
        self.assertEqual((self.tmp / "notes/note.md").read_text(), "hi\n")
        self.assertFalse((self.tmp / "notes/.exec").exists())
        self.assertTrue((Path(result["workspace"]) / ".exec").is_dir())


class PromptFileTests(unittest.TestCase):
    def test_the_workflow_names_prompt_files_that_exist(self):
        cfg = yaml.safe_load(CONFIG.read_text())
        loaded = prompts.resolve_all(cfg["prompts"])
        self.assertIn("{tools}", loaded["system"])
        self.assertIn("VERDICT: PASS", loaded["reviewer"])

    def test_a_plain_string_is_the_prompt_and_a_missing_file_is_an_error(self):
        self.assertEqual(prompts.load("be brief"), "be brief")
        with self.assertRaises(ValueError):
            prompts.load("file:nope.md")


class EventSchemaTests(unittest.TestCase):
    def test_validate_rejects_unknown_types_and_missing_fields(self):
        with self.assertRaises(ValueError):
            validate({"type": "nope"})
        with self.assertRaises(ValueError):
            validate({"type": "file.changed", "session": "s", "kind": "rename", "path": "a"})
        with self.assertRaises(ValueError):
            validate({"type": "step", "session": "s", "run": 1, "step_id": "act", "surprise": 1})
        validate({"type": "file.changed", "session": None, "kind": "move", "path": "a", "dest": "b"})


try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class NotesSearchTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock

        from agentic_ai.retrieval import rag
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "notes"
        (self.tmp / "session").mkdir()
        for rel, text in {"projects/alpha/budget.md": "# Budget\nAlpha needs 40 radios in 2569.",
                          "projects/beta/budget.md": "# Budget\nBeta needs 12 drones.",
                          "journal/day.md": "# Monday\nTalked about radios with the team."}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        self.patch = mock.patch.object(tools, "_embedder", rag.HashEmbedder)
        self.patch.start()
        self.ctx = ctx_for(self.root, rag={"type": "hybrid", "k": 4})

    def tearDown(self):
        self.patch.stop()
        index = tools._workspace_indexes.pop(str(self.root.resolve()), None)
        if index:
            index.close()
        shutil.rmtree(self.tmp)

    def test_search_cites_path_heading_and_line(self):
        out = tools.rag_search(self.ctx, "radios")
        self.assertTrue(out.startswith("found: [1] "))
        self.assertIn("projects/alpha/budget.md > Budget (line 1)", out)
        self.assertIn("journal/day.md", out)

    def test_folder_scope_from_the_argument_or_the_session(self):
        self.assertNotIn("journal", tools.rag_search(self.ctx, "radios", folder="projects"))
        self.assertNotIn("journal", tools.rag_search({**self.ctx, "scope": "projects/alpha"}, "radios"))
        self.assertIn("no passages", tools.rag_search(self.ctx, "radios", folder="projects/beta"))
        with self.assertRaises(ValueError):
            tools.rag_search(self.ctx, "radios", folder="nope")

    def test_a_tool_write_is_searchable_at_once(self):
        tools.rag_search(self.ctx, "radios")                     # the index exists and has scanned
        tools.write(self.ctx, "projects/gamma.md", "# Gamma\nGamma wants satellites.")
        self.assertIn("projects/gamma.md", tools.rag_search(self.ctx, "satellites"))
        tools.move(self.ctx, "projects/gamma.md", "journal")
        self.assertIn("journal/gamma.md", tools.rag_search(self.ctx, "satellites"))
        tools.delete(self.ctx, "journal/gamma.md")
        self.assertIn("no passages", tools.rag_search(self.ctx, "satellites"))


class ToolCallMarkupTests(unittest.TestCase):
    """Chat models sometimes answer in their native tool-call markup instead of the json asked for."""

    def test_qwen_function_markup(self):
        text = "<tool_call>\n<function=rag_search>\n<parameter=query>\nwho owns the radio contract\n</parameter>\n</function>\n</tool_call>"
        self.assertEqual(loop.parse_action(text), {"tool": "rag_search", "args": {"query": "who owns the radio contract"}})

    def test_hermes_json_inside_tool_call(self):
        text = '<tool_call>{"name": "read", "arguments": {"path": "a.md"}}</tool_call>'
        self.assertEqual(loop.parse_action(text), {"tool": "read", "args": {"path": "a.md"}})

    def test_the_broken_variant_seen_live(self):
        text = "<tool_call>\n<rag_search>\nquery>\nradio contract owner and delivery delays\n</parameter>\n</function>\n</tool_call>"
        self.assertEqual(loop.parse_action(text),
                         {"tool": "rag_search", "args": {"query": "radio contract owner and delivery delays"}})

    def test_json_still_wins_and_prose_still_fails(self):
        text = 'I will search.\n```json\n{"tool": "list", "args": {}}\n```\n<tool_call><function=read></function></tool_call>'
        self.assertEqual(loop.parse_action(text)["tool"], "list")
        with self.assertRaises(ValueError):
            loop.parse_action("I think the answer is 4.")
