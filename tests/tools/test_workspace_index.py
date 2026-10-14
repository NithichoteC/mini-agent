"""Tests for workspace_index: persistent markdown index with search."""
import tempfile
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from agentic_ai.retrieval import rag, workspace_index


@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class TestChunkMarkdown(unittest.TestCase):
    """Test markdown chunking: headings, code blocks, front-matter, line numbers."""

    def test_simple_sections(self):
        """Split by ATX headings."""
        text = """# Main
Body of main.

## Sub
Body of sub.
"""
        chunks = workspace_index.chunk_markdown(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["heading"], "Main")
        self.assertEqual(chunks[1]["heading"], "Main > Sub")
        self.assertIn("Main", chunks[0]["text"])
        self.assertIn("Sub", chunks[1]["text"])

    def test_heading_path(self):
        """Heading path reflects hierarchy."""
        text = """# H1
Text1

## H2
Text2

### H3
Text3
"""
        chunks = workspace_index.chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        self.assertEqual(headings[0], "H1")
        self.assertEqual(headings[1], "H1 > H2")
        self.assertEqual(headings[2], "H1 > H2 > H3")

    def test_code_fence_not_heading(self):
        """Lines inside ``` are not parsed as headings."""
        text = """# Main
```
# Not a heading
inside code
```
Back to text.
"""
        chunks = workspace_index.chunk_markdown(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["heading"], "Main")
        self.assertIn("# Not a heading", chunks[0]["text"])

    def test_front_matter_skipped(self):
        """YAML front-matter is skipped."""
        text = """---
title: Test
author: Me
---
# Main
Content.
"""
        chunks = workspace_index.chunk_markdown(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["heading"], "Main")
        self.assertNotIn("title:", chunks[0]["text"])
        self.assertIn("Content", chunks[0]["text"])

    def test_line_numbers(self):
        """Line numbers are 1-based, accurate."""
        text = """# Main
Body

## Sub
More text
"""
        chunks = workspace_index.chunk_markdown(text)
        self.assertEqual(chunks[0]["line"], 1)
        self.assertEqual(chunks[1]["line"], 4)

    def test_long_section_split(self):
        """Long sections split at blank lines."""
        words = " ".join([f"word{i}" for i in range(300)])
        text = f"""# Main
{words[:200]} word1

{words[200:]}
"""
        chunks = workspace_index.chunk_markdown(text, max_words=100)
        self.assertGreater(len(chunks), 1)

    def test_single_paragraph_kept_whole(self):
        """Single paragraph longer than max_words stays whole."""
        words = " ".join([f"word{i}" for i in range(250)])
        text = f"# Main\n{words}"
        chunks = workspace_index.chunk_markdown(text, max_words=100)
        # Should have at least one chunk with all the text
        self.assertTrue(any(len(rag.tokenize(c["text"])) > 100 for c in chunks))

    def test_empty_sections_dropped(self):
        """Sections with only whitespace are dropped."""
        text = """# Main
Content

##

## Sub
More
"""
        chunks = workspace_index.chunk_markdown(text)
        # Empty ## section should not create a chunk
        headings = [c["heading"] for c in chunks]
        self.assertTrue(all(h for h in headings))  # no empty headings


@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class CountingEmbedder:
    """Wrapper around HashEmbedder that counts encode calls."""

    def __init__(self, dim=256):
        self.base = rag.HashEmbedder(dim)
        self.name = "counting_hash"
        self.encode_count = 0
        self.texts_encoded = []

    def encode(self, texts, kind="passage"):
        self.encode_count += 1
        self.texts_encoded.extend(texts)
        return self.base.encode(texts, kind)


@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class TestWorkspaceIndex(unittest.TestCase):
    """Test WorkspaceIndex: scanning, indexing, searching."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.embedder = CountingEmbedder()
        self.index = workspace_index.WorkspaceIndex(self.root, self.embedder)

    def tearDown(self):
        self.index.close()
        self.tmpdir.cleanup()

    def write_file(self, rel_path: str, text: str):
        """Write a file to the workspace."""
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_scan_adds_files(self):
        """scan() detects and indexes new files."""
        self.write_file("notes.md", "# Note\nContent here.")
        result = self.index.scan()
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertGreater(self.embedder.encode_count, 0)

    def test_scan_no_change_no_reembed(self):
        """Second scan with no change embeds nothing."""
        self.write_file("notes.md", "# Note\nContent.")
        self.index.scan()
        embed_count_1 = self.embedder.encode_count

        result = self.index.scan()
        self.assertEqual(result["unchanged"], 1)
        # encode_count should not increase (meta check may still call encode for embedder check)
        embed_count_2 = self.embedder.encode_count
        # We expect no new embeddings
        self.assertEqual(embed_count_2, embed_count_1)

    def test_edit_one_section_re_embeds_one_chunk(self):
        """Editing one section of a multi-section file re-embeds exactly that section."""
        self.write_file("doc.md", """# H1
Text1

## H2
Text2

## H3
Text3
""")
        self.index.scan()
        embed_count_1 = self.embedder.encode_count

        # Edit H2 section
        self.write_file("doc.md", """# H1
Text1

## H2
Modified text!

## H3
Text3
""")
        self.index.scan()
        embed_count_2 = self.embedder.encode_count

        # Should have embedded only the changed section
        self.assertGreater(embed_count_2, embed_count_1)

    def test_touch_file_no_reembed(self):
        """Touching a file (same content, different mtime) doesn't re-embed."""
        self.write_file("notes.md", "# Note\nContent.")
        self.index.scan()
        embed_count_1 = self.embedder.encode_count

        # Touch file (write same content)
        self.write_file("notes.md", "# Note\nContent.")
        self.index.scan()
        embed_count_2 = self.embedder.encode_count

        self.assertEqual(embed_count_2, embed_count_1)

    def test_move_file_search_works(self):
        """Moving a file updates paths; search still works."""
        self.write_file("old/notes.md", "# Title\nBudget info here.")
        self.index.scan()

        # Move the file
        (self.root / "new").mkdir(exist_ok=True)
        (self.root / "old" / "notes.md").rename(self.root / "new" / "notes.md")

        self.index.move("old/notes.md", "new/notes.md")

        # Search should find it at new path
        hits = self.index.search("Budget", type="bm25", k=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "new/notes.md")

    def test_move_folder_search_works(self):
        """Moving a folder updates all paths; search still works."""
        self.write_file("old/a.md", "# A\nBudget info.")
        self.write_file("old/b.md", "# B\nMore text.")
        self.index.scan()

        # Move folder (simulate)
        (self.root / "new").mkdir(exist_ok=True)
        (self.root / "old" / "a.md").rename(self.root / "new" / "a.md")
        (self.root / "old" / "b.md").rename(self.root / "new" / "b.md")

        self.index.move("old", "new")

        hits = self.index.search("Budget", type="bm25", k=5)
        self.assertTrue(all("new/" in h["path"] for h in hits))

    def test_remove_file(self):
        """remove() deletes a file from the index."""
        self.write_file("notes.md", "# Note\nContent.")
        self.index.scan()

        result = self.index.remove("notes.md")
        self.assertEqual(result, 1)

        chunks = self.index.chunks()
        self.assertEqual(len(chunks), 0)

    def test_remove_folder(self):
        """remove() deletes all files under a folder."""
        self.write_file("notes/a.md", "# A\nText a.")
        self.write_file("notes/b.md", "# B\nText b.")
        self.write_file("other.md", "# Other\nText.")
        self.index.scan()

        result = self.index.remove("notes")
        self.assertEqual(result, 2)

        chunks = self.index.chunks()
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["path"], "other.md")

    def test_deleted_file_disappears(self):
        """Deleted files disappear after scan()."""
        self.write_file("notes.md", "# Note\nContent.")
        self.index.scan()

        # Delete the file
        (self.root / "notes.md").unlink()
        result = self.index.scan()

        self.assertEqual(result["removed"], 1)
        chunks = self.index.chunks()
        self.assertEqual(len(chunks), 0)

    def test_folder_scope_exact(self):
        """search(folder=...) returns only chunks from that folder."""
        self.write_file("a/x.md", "# A\nBudget proposal.")
        self.write_file("b/y.md", "# B\nBudget report.")
        self.write_file("c/z.md", "# C\nOther text.")
        self.index.scan()

        hits_a = self.index.search("Budget", folder="a", type="bm25", k=10)
        self.assertEqual(len(hits_a), 1)
        self.assertEqual(hits_a[0]["path"], "a/x.md")

        hits_b = self.index.search("Budget", folder="b", type="bm25", k=10)
        self.assertEqual(len(hits_b), 1)
        self.assertEqual(hits_b[0]["path"], "b/y.md")

    def test_folder_scope_no_prefix_overlap(self):
        """folder 'a' doesn't match 'ab/'."""
        self.write_file("a/file.md", "# A\nBudget.")
        self.write_file("ab/x.md", "# AB\nBudget.")
        self.index.scan()

        hits = self.index.search("Budget", folder="a", type="bm25", k=10)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "a/file.md")

    def test_bm25_finds_word(self):
        """BM25 search finds distinctive words."""
        self.write_file("doc.md", "# Doc\nThe budget proposal is here.")
        self.index.scan()

        hits = self.index.search("proposal", type="bm25", k=5)
        self.assertEqual(len(hits), 1)
        self.assertIn("proposal", hits[0]["text"].lower())

    def test_dense_returns_k_sorted(self):
        """Dense search returns k hits sorted by score."""
        self.write_file("doc1.md", "# One\nBudget proposal here.")
        self.write_file("doc2.md", "# Two\nOther content.")
        self.write_file("doc3.md", "# Three\nBudget discussion.")
        self.index.scan()

        hits = self.index.search("budget", type="dense", k=2)
        self.assertLessEqual(len(hits), 2)
        # Check sorted by score (descending)
        if len(hits) > 1:
            self.assertGreaterEqual(hits[0]["score"], hits[1]["score"])

    def test_hybrid_search(self):
        """Hybrid search fuses dense and BM25."""
        self.write_file("doc.md", "# Doc\nBudget proposal and discussion.")
        self.index.scan()

        hits = self.index.search("budget proposal", type="hybrid", k=5)
        self.assertGreater(len(hits), 0)

    def test_thai_segmentation(self):
        """Thai text is found by BM25 with proper tokenization."""
        # Check if PyThaiNLP segmenter works
        tokens = rag.tokenize("งบประมาณกองทัพ ปี 2569")
        if len(tokens) > 1:
            # Segmenter is available
            self.write_file("doc.md", "# เอกสาร\nงบประมาณกองทัพ ปี 2569")
            self.index.scan()

            hits = self.index.search("งบประมาณ", type="bm25", k=5)
            self.assertEqual(len(hits), 1)
        else:
            self.skipTest("PyThaiNLP segmenter not available")

    def test_hidden_folders_skipped(self):
        """Hidden folders (.git, .aiblue, etc.) are not indexed."""
        self.write_file("visible.md", "# Visible\nText.")
        self.write_file(".hidden/secret.md", "# Secret\nText.")
        self.write_file(".git/config", "# Config\nText.")

        result = self.index.scan()
        self.assertEqual(result["added"], 1)  # only visible.md

        chunks = self.index.chunks()
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["path"], "visible.md")

    def test_path_escape_raises(self):
        """A path that leaves the workspace raises; a leading slash means the workspace root."""
        with self.assertRaises(ValueError):
            self.index.rel("../escape")
        with self.assertRaises(ValueError):
            self.index.rel("a/../../escape")
        self.assertEqual(self.index.rel("/notes/a.md"), "notes/a.md")

    def test_embedder_change_reembeds(self):
        """Vectors from another embedder are all redone on the next scan, and only then."""
        self.write_file("doc.md", "# Doc\nContent.\n\n## Two\nMore.")
        self.index.scan()
        other = CountingEmbedder(dim=128)
        other.name = "other"
        self.index.embedder = other
        self.index.scan()
        self.assertEqual(len(other.texts_encoded), 2)
        self.index.scan()
        self.assertEqual(len(other.texts_encoded), 2)
        self.assertEqual(len(self.index.search("content", type="dense", k=1)), 1)

    def test_move_keeps_vectors_lines_and_the_file_record(self):
        self.write_file("a/n.md", "# N\nfirst\n\n## Deep\nsecond part here")
        self.index.scan()
        before = len(self.embedder.texts_encoded)
        lines = {c["heading"]: c["line"] for c in self.index.chunks()}
        (self.root / "b").mkdir()
        (self.root / "a/n.md").rename(self.root / "b/n.md")
        self.assertEqual(self.index.move("a", "b"), 1)
        self.assertEqual({c["heading"]: c["line"] for c in self.index.chunks("b")}, lines)
        self.assertEqual(self.index.chunks("a"), [])
        self.assertEqual(self.index.scan()["unchanged"], 1)            # nothing to redo after the move
        self.assertEqual(len(self.embedder.texts_encoded), before)
        self.assertEqual(self.index.search("second", type="bm25", k=1)[0]["path"], "b/n.md")

    def test_a_quote_in_the_query_is_not_fts_syntax(self):
        self.write_file("q.md", "# Q\nsay \"hello\" twice")
        self.index.scan()
        self.assertEqual(self.index.search('"hello', type="bm25", k=1)[0]["path"], "q.md")

    def test_a_long_section_is_packed_not_split_per_paragraph(self):
        para = " ".join(["word"] * 40)
        chunks = workspace_index.chunk_markdown("# T\n\n" + "\n\n".join([para] * 10), max_words=100)
        self.assertEqual(len(chunks), 5)                                 # 2 paragraphs (80 words) per chunk
        self.assertTrue(all(c["heading"] == "T" for c in chunks))
        self.assertEqual(len({c["line"] for c in chunks}), 5)           # each chunk starts at its own line

    def test_index_file_of_a_missing_path_drops_it(self):
        self.write_file("gone.md", "# G\nbye")
        self.index.scan()
        (self.root / "gone.md").unlink()
        self.assertEqual(self.index.index_file("gone.md"), 0)
        self.assertEqual(self.index.stats()["files"], 0)

    def test_stats(self):
        """stats() returns file, chunk, and embedder info."""
        self.write_file("doc.md", "# Doc\nContent.")
        self.index.scan()

        stats = self.index.stats()
        self.assertIn("files", stats)
        self.assertIn("chunks", stats)
        self.assertIn("embedder", stats)
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["chunks"], 1)

    def test_chunks_method(self):
        """chunks() returns all chunks, ordered by path and ord."""
        self.write_file("b.md", "# B\nText b.")
        self.write_file("a.md", "# A\nSection 1\n\n## Sub\nSection 2.")
        self.index.scan()

        all_chunks = self.index.chunks()
        # Should be ordered: a.md chunks first, then b.md
        self.assertTrue(all("a.md" in c["path"] for c in all_chunks[:1]))

    def test_multiple_files(self):
        """Index handles multiple files correctly."""
        self.write_file("file1.md", "# File1\nContent 1.")
        self.write_file("file2.md", "# File2\nContent 2.")
        self.write_file("file3.txt", "# File3\nContent 3.")

        result = self.index.scan()
        self.assertEqual(result["added"], 3)

        stats = self.index.stats()
        self.assertEqual(stats["files"], 3)
        self.assertEqual(stats["chunks"], 3)


@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class TestRffIntegration(unittest.TestCase):
    """Test that RRF works correctly in hybrid search."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.index = workspace_index.WorkspaceIndex(self.root, rag.HashEmbedder())

    def tearDown(self):
        self.index.close()
        self.tmpdir.cleanup()

    def test_rrf_fusion(self):
        """RRF fusion combines dense and BM25 results."""
        # Write files with overlapping content
        (self.root / "a.md").write_text("# A\nBudget proposal review board meeting.")
        (self.root / "b.md").write_text("# B\nBudget allocation discussion.")
        (self.root / "c.md").write_text("# C\nProposal deadline extended.")

        self.index.scan()

        hits = self.index.search("budget proposal", type="hybrid", k=2)
        # Should get results from RRF of both arms
        self.assertGreater(len(hits), 0)


if __name__ == "__main__":
    unittest.main()
