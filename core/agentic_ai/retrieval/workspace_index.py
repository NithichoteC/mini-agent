"""A persistent, incremental search index over the user's workspace: a folder of markdown notes.

    index = WorkspaceIndex(root, rag.E5())          root/.aiblue/index.db (created on first use)
    index.scan()                                    re-reads what changed on disk since the last scan
    index.index_file("ideas/a.md")                  after the app itself wrote a file
    index.move("ideas", "archive/ideas")            after a rename: paths change, vectors are kept
    index.search("budget 2569", k=5, folder="projects", type="hybrid")

One SQLite file holds everything: files (path, mtime, size, sha256), chunks (one per markdown section,
with its vector as a float32 blob) and an FTS5 table over the words of each chunk. Dense search is a
numpy dot product over the folder's vectors - exact, and plenty fast for a person's notes - so no
vector extension is needed, and the same tables map one-to-one onto PostgreSQL + pgvector later.

What keeps it cheap: a chunk's id is a hash of (path, heading, text), so an unchanged section keeps its
vector and only new or edited sections are embedded; scan() reads a file only when its mtime or size
moved, and re-chunks it only when its sha256 did. There is no file watcher (inotify does not see
changes on Windows drives under WSL2): the app re-indexes what its own tools write, and scan() catches
the rest. Thai words are segmented (rag.tokenize) before they reach FTS5, which only splits on spaces.
"""
import hashlib
import re
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from agentic_ai.retrieval import rag

SUFFIXES = (".md", ".txt")
MAX_WORDS = 200                     # a section longer than this is packed into several chunks
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, sha TEXT);
CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, path TEXT, heading TEXT, ord INTEGER, line INTEGER,
                                   text TEXT, vec BLOB);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, tokens,
                                                         tokenize = 'unicode61 remove_diacritics 0');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _words(text: str) -> int:
    return len(rag.tokenize(text))


def _pack(paragraphs: list[tuple[int, str]], max_words: int) -> list[tuple[int, str]]:
    """Consecutive paragraphs joined until the next one would pass max_words; (first line, text) each.
    A single paragraph longer than max_words stays whole: cutting mid-paragraph loses more than it saves."""
    out, line, buf, n = [], 0, [], 0
    for start, para in paragraphs:
        w = _words(para)
        if buf and n + w > max_words:
            out.append((line, "\n\n".join(buf)))
            buf, n = [], 0
        if not buf:
            line = start
        buf.append(para)
        n += w
    if buf:
        out.append((line, "\n\n".join(buf)))
    return out


def chunk_markdown(text: str, max_words: int = MAX_WORDS) -> list[dict]:
    """Sections by ATX heading (not inside ``` fences), front matter skipped. One dict per chunk:
    heading ("H1 > H2", "" before the first heading), ord, line (1-based start), text (with its heading line)."""
    lines = text.split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
        if end is not None:
            start = end + 1
    sections, stack, current, fence = [], [], {"heading": "", "line": start + 1, "lines": []}, None
    for i in range(start, len(lines)):
        line = lines[i]
        m = FENCE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0]
            elif mark[0] == fence:
                fence = None
        h = HEADING.match(line) if fence is None else None
        if h:
            sections.append(current)
            level = len(h.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, h.group(2)))
            current = {"heading": " > ".join(t for _, t in stack), "line": i + 1, "lines": [line]}
        else:
            current["lines"].append(line)
    sections.append(current)

    chunks = []
    for sec in sections:
        body = "\n".join(sec["lines"]).strip()
        if not body:                    # a heading alone stays: a title is worth finding
            continue
        if _words(body) <= max_words:
            parts = [(sec["line"] + _leading_blank(sec["lines"]), body)]
        else:
            paragraphs, para, para_line = [], [], None
            for j, line in enumerate(sec["lines"]):
                if line.strip():
                    if not para:
                        para_line = sec["line"] + j
                    para.append(line)
                elif para:
                    paragraphs.append((para_line, "\n".join(para).strip()))
                    para = []
            if para:
                paragraphs.append((para_line, "\n".join(para).strip()))
            parts = _pack(paragraphs, max_words)
        for line, part in parts:
            chunks.append({"heading": sec["heading"], "ord": len(chunks), "line": line, "text": part})
    return chunks


def _leading_blank(lines: list[str]) -> int:
    return next((i for i, line in enumerate(lines) if line.strip()), 0)


def chunk_id(path: str, heading: str, text: str) -> str:
    return hashlib.sha1(f"{path}\0{heading}\0{text}".encode()).hexdigest()[:16]


def _like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


class WorkspaceIndex:
    def __init__(self, root, embedder, db_path=None):
        self.root = Path(root).resolve()
        self.embedder = embedder
        path = Path(db_path) if db_path else self.root / ".aiblue" / "index.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path
        self.lock = threading.RLock()             # the web backend calls in from worker threads
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._dense = {}                          # folder -> (rows, matrix), dropped on any write

    # ---- paths ----------------------------------------------------------------------------------
    def rel(self, path: str) -> str:
        """A workspace-relative path with "/" separators; ValueError when it leaves the workspace."""
        p = (self.root / str(path).strip("/")).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"path '{path}' is outside the workspace")
        return p.relative_to(self.root).as_posix() if p != self.root else ""

    def indexed(self, rel: str) -> bool:
        """Notes are *.md / *.txt outside dot-folders (.aiblue, .git, .obsidian)."""
        return rel.endswith(SUFFIXES) and not any(part.startswith(".") for part in rel.split("/"))

    def _scope(self, folder: str, column: str = "path") -> tuple[str, list]:
        folder = self.rel(folder) if folder else ""
        if not folder:
            return "", []
        return f" AND ({column} = ? OR {column} LIKE ? ESCAPE '\\')", [folder, _like(folder)]

    # ---- writing --------------------------------------------------------------------------------
    def _meta_ok(self) -> bool:
        """False when the stored vectors came from another embedder: they must all be redone."""
        rows = dict(self.conn.execute("SELECT key, value FROM meta"))
        return not rows or rows.get("embedder") == self.embedder.name

    def scan(self) -> dict:
        """Bring the index in line with the disk. Returns counts of files added / updated / removed / unchanged."""
        with self.lock:
            counts = dict(added=0, updated=0, removed=0, unchanged=0)
            if not self._meta_ok():                          # new embedder: every vector is redone below
                self.conn.execute("UPDATE chunks SET vec = NULL")
            known = {r[0]: r[1:] for r in self.conn.execute("SELECT path, mtime_ns, size, sha FROM files")}
            seen = set()
            for p in self.root.rglob("*"):
                rel = p.relative_to(self.root).as_posix()
                if not p.is_file() or not self.indexed(rel):
                    continue
                seen.add(rel)
                st = p.stat()
                old = known.get(rel)
                if old and (old[0], old[1]) == (st.st_mtime_ns, st.st_size):
                    counts["unchanged"] += 1
                    continue
                data = p.read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                if old and old[2] == sha:                     # touched, not changed
                    self.conn.execute("UPDATE files SET mtime_ns = ?, size = ? WHERE path = ?",
                                      (st.st_mtime_ns, st.st_size, rel))
                    counts["unchanged"] += 1
                    continue
                self._index(rel, data.decode("utf-8", errors="replace"), st, sha)
                counts["updated" if old else "added"] += 1
            for rel in set(known) - seen:
                self._drop(rel)
                counts["removed"] += 1
            missing = [r[0] for r in self.conn.execute("SELECT DISTINCT path FROM chunks WHERE vec IS NULL")]
            for rel in missing:                               # after an embedder change
                self._embed_missing(rel)
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('embedder', ?)", (self.embedder.name,))
            self.conn.commit()
            self._dense.clear()
            return counts

    def index_file(self, path: str) -> int:
        """(Re)index one file the app just wrote; returns how many chunks had to be embedded.
        A path that no longer exists, or is not a note, is dropped from the index instead."""
        with self.lock:
            rel = self.rel(path)
            p = self.root / rel
            if not p.is_file() or not self.indexed(rel):
                self._drop(rel)
                self.conn.commit()
                self._dense.clear()
                return 0
            data = p.read_bytes()
            n = self._index(rel, data.decode("utf-8", errors="replace"), p.stat(), hashlib.sha256(data).hexdigest())
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('embedder', ?)", (self.embedder.name,))
            self.conn.commit()
            self._dense.clear()
            return n

    def _index(self, rel: str, text: str, st, sha: str) -> int:
        chunks = chunk_markdown(text)
        old = {r[0]: r[1] for r in self.conn.execute("SELECT id, vec FROM chunks WHERE path = ?", (rel,))}
        rows = [(chunk_id(rel, c["heading"], c["text"]), c) for c in chunks]
        rows = list({cid: c for cid, c in rows}.items())      # a section repeated word for word counts once
        fresh = [(cid, c) for cid, c in rows if old.get(cid) is None]
        vectors = {}
        if fresh:
            vecs = self.embedder.encode([self._embed_text(rel, c["heading"], c["text"]) for _, c in fresh], "passage")
            vectors = {cid: vecs[i].astype("float32").tobytes() for i, (cid, _) in enumerate(fresh)}
        gone = set(old) - {cid for cid, _ in rows}
        self.conn.executemany("DELETE FROM chunks WHERE id = ?", [(cid,) for cid in gone])
        self.conn.executemany("DELETE FROM chunks_fts WHERE id = ?", [(cid,) for cid in gone])
        for cid, c in rows:
            self.conn.execute("INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (cid, rel, c["heading"], c["ord"], c["line"], c["text"], vectors.get(cid, old.get(cid))))
            if cid not in old:
                self.conn.execute("INSERT INTO chunks_fts VALUES (?, ?)", (cid, self._tokens(rel, c["heading"], c["text"])))
        self.conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)", (rel, st.st_mtime_ns, st.st_size, sha))
        return len(fresh)

    def _embed_missing(self, rel: str) -> None:
        rows = list(self.conn.execute("SELECT id, heading, text FROM chunks WHERE path = ? AND vec IS NULL", (rel,)))
        if rows:
            vecs = self.embedder.encode([self._embed_text(rel, h, t) for _, h, t in rows], "passage")
            self.conn.executemany("UPDATE chunks SET vec = ? WHERE id = ?",
                                  [(vecs[i].astype("float32").tobytes(), cid) for i, (cid, _, _) in enumerate(rows)])

    @staticmethod
    def _embed_text(rel: str, heading: str, text: str) -> str:
        # the note's path and heading carry meaning the section text often leaves out ("Budget" under projects/alpha)
        return f"{rel} > {heading}\n{text}" if heading else f"{rel}\n{text}"

    @staticmethod
    def _tokens(rel: str, heading: str, text: str) -> str:
        return " ".join(rag.tokenize(f"{rel.replace('/', ' ')} {heading} {text}"))

    def _drop(self, rel: str) -> None:
        ids = [(r[0],) for r in self.conn.execute("SELECT id FROM chunks WHERE path = ?", (rel,))]
        self.conn.executemany("DELETE FROM chunks_fts WHERE id = ?", ids)
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        self.conn.execute("DELETE FROM files WHERE path = ?", (rel,))

    def _paths_under(self, rel: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT path FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                                                (rel, _like(rel)))]

    def remove(self, path: str) -> int:
        """A deleted file, or a folder (every note under it). Returns how many files left the index."""
        with self.lock:
            paths = self._paths_under(self.rel(path))
            for rel in paths:
                self._drop(rel)
            self.conn.commit()
            self._dense.clear()
            return len(paths)

    def move(self, old: str, new: str) -> int:
        """A renamed or moved file or folder: rows follow it with their vectors (no embedding).
        Chunk ids include the path, so they are recomputed; the FTS rows are rewritten with the new path."""
        with self.lock:
            old, new = self.rel(old), self.rel(new)
            paths = self._paths_under(old)
            for src in paths:
                dst = new if src == old else new + src[len(old):]
                if not self.indexed(dst):                    # renamed to something that is not a note
                    self._drop(src)
                    continue
                self._drop_path(dst)
                for cid, heading, ord_, line, text, vec in list(self.conn.execute(
                        "SELECT id, heading, ord, line, text, vec FROM chunks WHERE path = ?", (src,))):
                    nid = chunk_id(dst, heading, text)
                    self.conn.execute("DELETE FROM chunks WHERE id = ?", (cid,))
                    self.conn.execute("DELETE FROM chunks_fts WHERE id = ?", (cid,))
                    self.conn.execute("INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                                      (nid, dst, heading, ord_, line, text, vec))
                    self.conn.execute("INSERT INTO chunks_fts VALUES (?, ?)", (nid, self._tokens(dst, heading, text)))
                self.conn.execute("UPDATE files SET path = ? WHERE path = ?", (dst, src))
            self.conn.commit()
            self._dense.clear()
            return len(paths)

    def _drop_path(self, rel: str) -> None:
        if self.conn.execute("SELECT 1 FROM files WHERE path = ?", (rel,)).fetchone():
            self._drop(rel)

    # ---- reading --------------------------------------------------------------------------------
    def search(self, query: str, k: int = 5, folder: str = "", type: str = "hybrid") -> list[dict]:
        """Up to k hits {id, path, heading, line, text, score}; folder limits them to that folder."""
        if type not in ("dense", "bm25", "hybrid"):
            raise ValueError(f"unknown search type '{type}' (dense, bm25 or hybrid)")
        with self.lock:
            where, args = self._scope(folder)
            if type == "dense":
                return self._dense_hits(query, k, folder, where, args)
            if type == "bm25":
                return self._bm25_hits(query, k, where, args)
            lists = [self._dense_hits(query, rag.CANDIDATES, folder, where, args),
                     self._bm25_hits(query, rag.CANDIDATES, where, args)]
            fused, by_id = defaultdict(float), {}
            for hits in lists:                               # reciprocal rank fusion, as rag.rrf
                for rank, h in enumerate(hits, 1):
                    fused[h["id"]] += 1 / (rag.RRF_K + rank)
                    by_id.setdefault(h["id"], h)
            best = sorted(fused, key=fused.get, reverse=True)[:k]
            return [{**by_id[cid], "score": round(fused[cid], 6)} for cid in best]

    def _dense_hits(self, query, k, folder, where, args) -> list[dict]:
        import numpy as np
        if folder not in self._dense:
            rows = list(self.conn.execute(
                f"SELECT id, path, heading, line, text, vec FROM chunks WHERE vec IS NOT NULL{where}", args))
            matrix = np.stack([np.frombuffer(r[5], dtype="float32") for r in rows]) if rows else None
            self._dense[folder] = (rows, matrix)
        rows, matrix = self._dense[folder]
        if matrix is None:
            return []
        q = self.embedder.encode([query], "query")[0]
        if q.shape[0] != matrix.shape[1]:
            raise ValueError("the index holds vectors from another embedder; run scan() first")
        scores = matrix @ q
        top = np.argsort(-scores)[:k]
        return [self._hit(rows[i], float(scores[i])) for i in top]

    def _bm25_hits(self, query, k, where, args) -> list[dict]:
        words = rag.tokenize(query)
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in dict.fromkeys(words))
        rows = self.conn.execute(
            "SELECT c.id, c.path, c.heading, c.line, c.text, bm25(chunks_fts) AS s FROM chunks_fts "
            f"JOIN chunks c ON c.id = chunks_fts.id WHERE chunks_fts MATCH ?{where.replace('path', 'c.path')} "
            "ORDER BY s LIMIT ?", [match, *args, k])
        return [self._hit(r, -float(r[5])) for r in rows]       # FTS5's bm25() is lower-is-better

    @staticmethod
    def _hit(row, score: float) -> dict:
        return {"id": row[0], "path": row[1], "heading": row[2], "line": row[3], "text": row[4], "score": score}

    def chunks(self, folder: str = "") -> list[dict]:
        """Every chunk under folder (all when ""), in reading order - what the graph builder reads."""
        with self.lock:
            where, args = self._scope(folder)
            rows = self.conn.execute(f"SELECT id, path, heading, ord, line, text FROM chunks WHERE 1=1{where} "
                                     "ORDER BY path, ord", args)
            return [dict(zip(("id", "path", "heading", "ord", "line", "text"), r)) for r in rows]

    def stats(self) -> dict:
        with self.lock:
            files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            return {"files": files, "chunks": chunks, "embedder": self.embedder.name}

    def close(self) -> None:
        with self.lock:
            self.conn.close()
