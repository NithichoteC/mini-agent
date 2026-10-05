"""Offline tests for rag.py: no model download, no network. Skipped when the RAG extras are missing.

    pip install numpy faiss-cpu pymupdf     (requirements-rag.txt has the full set)
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import rag

HAVE = all(importlib.util.find_spec(m) for m in ("numpy", "faiss", "pymupdf"))

DOCS = [
    ("budget.pdf", 1, "The Royal Thai Navy requested 4,200 million baht for coastal radar in fiscal year 2026."),
    ("budget.pdf", 2, "Committee members asked why the radar contract was signed before the Cabinet approved it."),
    ("carbon.pdf", 1, "Thailand plans a carbon tax on fuels, starting at 200 baht per tonne of CO2."),
    ("carbon.pdf", 2, "An emissions trading system would cover power and heavy industry after 2028."),
    ("growth.pdf", 1, "Startups in Thailand face limited venture capital and slow business registration."),
    ("growth.pdf", 2, "SMEs account for most employment but a small share of exports."),
]


def corpus():
    return [{"id": i, "file": f, "page": p, "text": t} for i, (f, p, t) in enumerate(DOCS)]


def fake_llm(*replies):
    it = iter(replies)
    return lambda prompt: {"ok": True, "text": next(it), "usage": {"total_tokens": 7}}


class Text(unittest.TestCase):
    def test_chunk_overlaps_and_ends_without_a_duplicate_tail(self):
        pages = [{"file": "a", "page": 1, "text": " ".join(f"w{i}" for i in range(500))}]
        chunks = rag.chunk(pages, words=260, overlap=40)
        self.assertEqual([len(c["text"].split()) for c in chunks], [260, 260, 60])
        self.assertEqual(chunks[1]["text"].split()[0], "w220")
        self.assertEqual(chunks[-1]["text"].split()[-1], "w499")

    def test_chunk_keeps_file_and_page_and_numbers_ids(self):
        chunks = rag.chunk([{"file": "a", "page": 3, "text": "x y"}, {"file": "b", "page": 1, "text": ""}])
        self.assertEqual(chunks, [{"id": 0, "file": "a", "page": 3, "text": "x y"}])

    def test_the_regex_tokenizer_cuts_thai_at_vowel_and_tone_marks(self):
        with mock.patch.object(rag, "SEGMENT_THAI", False):
            self.assertEqual(rag.tokenize("คาดว่าจะขยายตัว"), ["คาดว", "าจะขยายต"])

    @unittest.skipUnless(importlib.util.find_spec("pythainlp"), "pythainlp not installed")
    def test_thai_is_segmented_into_words_and_digits_folded(self):
        self.assertEqual(rag.tokenize("ปีงบประมาณ ๒๕69 คาดว่าจะขยายตัว"),
                         ["ปีงบประมาณ", "2569", "คาด", "ว่า", "จะ", "ขยายตัว"])

    def test_needs_ocr_on_short_or_symbol_pages(self):
        self.assertTrue(rag.needs_ocr("12"))
        self.assertTrue(rag.needs_ocr("|-.-|" * 40))
        self.assertFalse(rag.needs_ocr("a real paragraph of text " * 10))

    def test_drop_repeats_cuts_a_loop_but_keeps_short_cells(self):
        loop = "The guardrail debate needs proportion, not inflation. " * 3
        text, looped = rag.drop_repeats(loop + "\n0.0 | 0.0\n0.0 | 0.0")
        self.assertTrue(looped)
        self.assertEqual(text, "The guardrail debate needs proportion, not inflation.\n0.0 | 0.0\n0.0 | 0.0")
        self.assertEqual(rag.drop_repeats("one line\n\nanother"), ("one line\n\nanother", False))

    def test_bm25_never_scores_a_match_below_a_miss(self):
        # "carbon" is in 2 of 3 passages: rank_bm25's idf goes negative here and ranks the miss first
        bm25 = rag.BM25([["radar", "procurement"], ["carbon", "tax"], ["carbon", "schedule"]])
        scores = bm25.get_scores(["carbon", "tax"])
        self.assertEqual(scores[0], 0.0)
        self.assertGreater(scores[2], 0.0)
        self.assertGreater(scores[1], scores[2])

    def test_rrf_rewards_items_both_lists_rank_high(self):
        self.assertEqual(rag.rrf([[1, 2, 3], [3, 1, 9]], k=2), [1, 3])

    def test_extract_reads_a_text_file_as_one_page(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "n.md").write_text("# Notes\n\n\n\nline   two", encoding="utf-8")
        self.assertEqual(rag.extract(tmp / "n.md"),
                         [{"file": "n.md", "page": 1, "method": "text", "text": "# Notes\n\nline two"}])

    def test_ask_json_survives_prose_and_garbage(self):
        t = rag.Stages()
        self.assertEqual(rag.ask_json(fake_llm('Sure: {"query": "x"} done'), t, "p"), {"query": "x"})
        self.assertEqual(rag.ask_json(fake_llm("no json here"), t, "p"), {})
        self.assertEqual(rag.ask_json(lambda p: {"ok": False, "error": "429"}, t, "p"), {})
        self.assertEqual(t.tokens, 14)


@unittest.skipUnless(HAVE, "RAG extras not installed")
class Retrieval(unittest.TestCase):
    def setUp(self):
        self.index = rag.Index.build(corpus(), rag.HashEmbedder())

    def files(self, query, type, **kw):
        return [(h["file"], h["page"]) for h in self.index.search(query, type, k=2, **kw)["hits"]]

    def test_every_rule_type_finds_the_page_with_the_words(self):
        for type in ("dense", "bm25", "hybrid", "graph", "agentic", "corrective"):
            with self.subTest(type=type):
                self.assertEqual(self.files("carbon tax per tonne", type)[0], ("carbon.pdf", 1))

    def test_search_reports_stage_timings(self):
        r = self.index.search("radar contract", "hybrid", k=3)
        self.assertEqual(set(r["stages"]), {"text_encode", "dense", "bm25", "fusion"})
        self.assertEqual(len(r["hits"]), 3)

    def test_graph_links_chunks_that_name_the_same_entity(self):
        self.assertEqual(self.index.graph, {2: {4}, 4: {2}})   # both name "Thailand"; no other entity repeats
        ids = [h["id"] for h in self.index.search("carbon tax", "graph", k=6)["hits"]]
        self.assertEqual(sorted(ids), list(range(6)))      # seeds are all six here; expansion adds nothing new

    def test_unknown_type_and_missing_llm_are_errors(self):
        with self.assertRaises(ValueError):
            self.index.search("x", "fusion-magic")
        with self.assertRaises(ValueError):
            self.index.search("x", "agentic-llm")
        with self.assertRaises(ValueError):
            self.index.search("x", "multimodal")           # no visual index was built

    def test_agentic_llm_searches_with_the_rewritten_query(self):
        llm = fake_llm('{"query": "venture capital startups", "keywords": false}')
        r = self.index.search("why is it hard to fund new firms?", "agentic-llm", k=1, llm=llm)
        self.assertEqual((r["hits"][0]["file"], r["hits"][0]["page"]), ("growth.pdf", 1))
        self.assertEqual(r["llm_tokens"], 7)
        self.assertNotIn("bm25", r["stages"])

    def test_corrective_llm_keeps_graded_passages_first(self):
        top = [h["id"] for h in self.index.search("radar budget", "dense", k=2)["hits"]]
        llm = fake_llm('{"relevant": [2, 1]}')
        r = self.index.search("radar budget", "corrective-llm", k=2, llm=llm)
        self.assertEqual([h["id"] for h in r["hits"]], top)
        self.assertNotIn("llm_rewrite", r["stages"])

    def test_corrective_llm_rewrites_when_nothing_is_relevant(self):
        llm = fake_llm('{"relevant": []}', '{"query": "emissions trading"}', '{"relevant": [1]}')
        r = self.index.search("what about pollution permits?", "corrective-llm", k=2, llm=llm)
        self.assertEqual(r["hits"][0]["file"], "carbon.pdf")
        self.assertIn("llm_rewrite", r["stages"])
        self.assertEqual(r["llm_tokens"], 21)

    def test_save_and_load_round_trip(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.index.save(tmp)
        again = rag.Index.load(tmp, rag.HashEmbedder())
        self.assertEqual(again.search("emissions trading", "hybrid", k=1)["hits"],
                         self.index.search("emissions trading", "hybrid", k=1)["hits"])
        with self.assertRaises(ValueError):
            rag.Index.load(tmp, SimpleNamespace(name="e5"))

    def test_sizes_cover_every_structure(self):
        self.assertEqual(set(self.index.sizes()), {"dense", "bm25", "graph"})


@unittest.skipUnless(HAVE, "RAG extras not installed")
class Extraction(unittest.TestCase):
    def setUp(self):
        import pymupdf
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Coastal radar programme, 4,200 million baht. " * 5)
        doc.new_page()                                    # blank: a scanned page with no text layer
        doc.save(self.tmp / "s.pdf")

    def test_only_the_empty_page_goes_to_ocr(self):
        calls = []

        def ocr(png):
            calls.append(png[:8])
            return {"ok": True, "text": "read by OCR", "sec": 0.5, "cached": False,
                    "input_tokens": 1800, "output_tokens": 5}
        pages = rag.extract(self.tmp / "s.pdf", ocr=ocr)
        self.assertEqual([p["method"] for p in pages], ["native", "ocr"])
        self.assertEqual(pages[1]["text"], "read by OCR")
        self.assertEqual(calls, [b"\x89PNG\r\n\x1a\n"])

    def test_a_read_that_hits_the_cap_is_read_again_in_two_strips(self):
        replies = iter([rag.OCR_MAX_TOKENS, 400, 300])

        def ocr(png):
            return {"ok": True, "text": f"strip of {len(png)} bytes", "sec": 1.0, "cached": False,
                    "input_tokens": 1800, "output_tokens": next(replies)}
        ocr.tiles, ocr.split_on_cap = 1, True
        import pymupdf
        r = rag.ocr_page(pymupdf.open(self.tmp / "s.pdf")[0], ocr)
        self.assertTrue(r["split"])
        self.assertEqual((r["tiles"], r["input_tokens"], r["output_tokens"]), (2, 5400, 1700))
        self.assertEqual(len(r["text"].splitlines()), 2)

    def test_headings_are_the_larger_lines_and_a_wrapped_one_is_joined(self):
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 60), "Part 1. Recent Developments and", fontsize=18)
        page.insert_text((72, 82), "the Outlook for Growth", fontsize=18)
        for i in range(30):
            page.insert_text((72, 110 + i * 14), f"Body line {i} with ordinary words in it.", fontsize=11)
        page.insert_text((72, 540), "Annex: The Model", fontsize=18)
        doc.save(self.tmp / "h.pdf")
        self.assertEqual(rag.headings(self.tmp / "h.pdf"),
                         [(1, "Part 1. Recent Developments and the Outlook for Growth"), (1, "Annex: The Model")])
        self.assertEqual(rag.headings(self.tmp / "notes.txt"), [])

    def test_without_ocr_the_page_is_marked_not_dropped(self):
        pages = rag.extract(self.tmp / "s.pdf")
        self.assertEqual(pages[1]["method"], "native, needs OCR")

    def test_groq_ocr_caches_and_never_pays_twice(self):
        reply = {"ok": True, "text": "page text", "model": "m", "usage": {"prompt_tokens": 1827, "completion_tokens": 9}}
        with mock.patch("llm_handler.call_llm", return_value=reply) as call:
            ocr = rag.groq_ocr(self.tmp / "cache")
            first, second = ocr(b"png-bytes"), ocr(b"png-bytes")
        self.assertEqual(call.call_count, 1)
        self.assertEqual((first["cached"], second["cached"]), (False, True))
        self.assertEqual(second["input_tokens"], 1827)
        content = call.call_args[0][0][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(call.call_args[1]["role"], "ocr")

    def test_groq_ocr_failure_is_reported_not_cached(self):
        with mock.patch("llm_handler.call_llm", return_value={"ok": False, "error": "http_error"}):
            r = rag.groq_ocr(self.tmp / "cache")(b"x")
        self.assertEqual(r, {"ok": False, "error": "http_error"})
        self.assertEqual(list((self.tmp / "cache").iterdir()), [])


class Attach(unittest.TestCase):
    """tools.attach / tools.rag_search: what a user's file becomes inside a session."""
    def setUp(self):
        import tools
        self.tools = tools
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.ctx = {"workspace": self.ws, "sandbox": {"max_output_chars": 4000},
                    "rag": {"full_text_tokens": 50, "type": "hybrid", "k": 2, "ocr": False}}
        patcher = mock.patch.object(tools, "_embedder", rag.HashEmbedder)   # no model download in tests
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(tools._indexes.clear)

    def file(self, name, text):
        (self.tmp / name).write_text(text, encoding="utf-8")
        return self.tmp / name

    def test_a_short_file_goes_into_the_message_whole(self):
        note = self.tools.attach(self.ctx, self.file("memo.txt", "Budget hearing moved to Friday."))
        self.assertTrue(note.startswith("Attached file memo.txt (1 page(s), about"))
        self.assertIn("[memo.txt p.1]\nBudget hearing moved to Friday.", note)
        self.assertTrue((self.ws / "memo.txt").exists())
        self.assertFalse((self.ws / ".rag").exists())

    @unittest.skipUnless(HAVE, "RAG extras not installed")
    def test_a_long_file_is_indexed_and_searchable(self):
        body = " ".join(t for _, _, t in DOCS) * 3
        note = self.tools.attach(self.ctx, self.file("notes.txt", body))
        self.assertIn("it is indexed", note)
        self.assertIn("\nOutline:\n- p.1: The Royal Thai Navy requested", note)
        out = self.tools.rag_search(self.ctx, "emissions trading system")
        self.assertTrue(out.startswith("found: [1] notes.txt p.1\n\n[1] notes.txt p.1\n"))   # 232 words: one passage
        self.assertIn("emissions trading", out)
        self.assertEqual(self.tools.glob(self.ctx).split()[0], "notes.txt")     # .rag/ stays hidden

    @unittest.skipUnless(HAVE, "RAG extras not installed")
    def test_two_attachments_share_one_index_and_survive_a_reload(self):
        self.tools.attach(self.ctx, self.file("a.txt", "radar procurement " * 100))
        self.tools.attach(self.ctx, self.file("b.txt", "carbon tax schedule " * 100))
        self.tools._indexes.clear()                          # a new process: load from .rag/
        out = self.tools.rag_search(self.ctx, "carbon tax", k=1)
        self.assertTrue(out.startswith("found: [1] b.txt p.1\n"))
        files = {c["file"] for c in self.tools._indexes[str(self.ws)].chunks}
        self.assertEqual(files, {"a.txt", "b.txt"})

    def test_reading_the_attached_pdf_points_to_rag_search(self):
        (self.ws / "r.pdf").write_bytes(b"%PDF-1.7\n\xe2\x80\xff binary")
        with self.assertRaisesRegex(ValueError, "not a text file .* rag_search"):
            self.tools.read(self.ctx, "r.pdf")

    @unittest.skipUnless(HAVE, "RAG extras not installed")
    def test_a_scanned_page_is_named_when_ocr_is_off(self):
        import pymupdf
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Coastal radar programme, 4,200 million baht. " * 5)
        doc.new_page()                                        # no text layer
        doc.save(self.tmp / "scan.pdf")
        note = self.tools.attach(self.ctx, self.tmp / "scan.pdf")
        self.assertIn("page(s) 2 have no text layer (scanned or images) and were not read: OCR is off (rag.ocr)", note)

    @unittest.skipUnless(HAVE, "RAG extras not installed")
    def test_ocr_passages_are_labelled_in_full_text_and_in_search(self):
        pages = [{"file": "s.pdf", "page": 1, "method": "ocr", "text": "budget 1,475.0 million baht"},
                 {"file": "s.pdf", "page": 2, "method": "native", "text": "carbon tax schedule"}]
        with mock.patch.object(rag, "extract", return_value=pages):
            note = self.tools.attach(self.ctx, self.file("s.pdf", "x"))
        self.assertIn("[s.pdf p.1, read by OCR - check numbers]\nbudget", note)
        self.assertIn("[s.pdf p.2]\ncarbon", note)
        self.ctx["rag"]["full_text_tokens"] = 1                # force the index path
        with mock.patch.object(rag, "extract", return_value=pages):
            self.tools.attach(self.ctx, self.file("s2.pdf", "x"))
        out = self.tools.rag_search(self.ctx, "budget million baht", k=1)
        self.assertIn("[1] s.pdf p.1, read by OCR - check numbers\nbudget", out)

    def test_search_without_an_index_tells_the_model_why(self):
        with self.assertRaisesRegex(ValueError, "no attached document is indexed"):
            self.tools.rag_search(self.ctx, "anything")

    def test_without_the_extras_a_long_file_is_cut_and_says_so(self):
        with mock.patch.object(self.tools, "_rag_add", side_effect=ImportError("x", name="faiss")):
            note = self.tools.attach(self.ctx, self.file("long.txt", "word " * 1000))
        self.assertIn("Only the first 50 tokens fit here and search is not installed (faiss)", note)
        self.assertTrue(note.endswith("[... the rest of the file is cut]"))

    def test_an_unreadable_pdf_is_reported_not_raised(self):
        note = self.tools.attach(self.ctx, self.file("broken.pdf", "not really a pdf"))
        self.assertTrue(note.startswith("Attached file broken.pdf:"))

    @unittest.skipUnless(HAVE, "RAG extras not installed")
    def test_the_agent_searches_an_attached_file_in_a_real_loop(self):
        import loop
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config/workflow.yaml").read_text())
        cfg["sandbox"]["dir"], cfg["trace"]["enabled"] = str(self.tmp), False
        cfg["rag"] = self.ctx["rag"]
        replies = iter(['```json\n{"tool": "rag_search", "args": {"query": "carbon tax per tonne"}}\n```',
                        '```json\n{"tool": "final_answer", "args": {"answer": "200 baht (notes.txt p.1)"}}\n```',
                        "VERDICT: PASS"])
        seen = []
        with mock.patch.object(loop, "call_llm", lambda messages, **kw: (
                seen.append(messages[-1]["content"]), {"ok": True, "text": next(replies), "usage": {}})[1]):
            result = loop.run_workflow(cfg, "What is the carbon tax rate?",
                                       attachments=[self.file("notes.txt", " ".join(t for _, _, t in DOCS) * 3)])
        self.assertEqual(result["status"], "done")
        self.assertIn("use rag_search", seen[0])
        self.assertIn("200 baht per tonne", seen[1])         # the observation the model got back


if __name__ == "__main__":
    unittest.main()
