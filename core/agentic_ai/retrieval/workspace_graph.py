"""The knowledge graph of the notes workspace, kept next to its search index (same index.db).

Two kinds of edges:
    links      [[note name]] between notes - parsed from the text on every call, free and always current
    relations  (source, relation, target, evidence) triples a model reads out of each section with the
               course's graph library (integrations/knowledge_graph); they cost tokens, so they are
               extracted only when asked (build) and only for sections not read before

A section is remembered by a hash of its heading and text, not its path: moving or renaming a note costs
nothing, an edited section is read again, and a deleted one simply stops being joined in. Nothing is
extracted silently - build() reports what it read, what it spent and what it could not read.

    graph = WorkspaceGraph(index)
    graph.pending("projects")                      sections in that folder not read yet
    graph.build("projects", progress=print)        read them, a few sections per model call
    graph.view("projects")                         nodes and edges for the UI
    graph.context("who owns the radio contract", "projects", hits)    evidence lines for rag_search
"""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime

from agentic_ai.integrations.knowledge_graph import (
    build_graph,
    extract_paths_batch,
    normalize_entities,
    normalize_entity_key,
    retrieve_subgraph,
)

WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
BATCH_CHARS = 6000          # section text per extraction call (several sections share one prompt)

SCHEMA = """
CREATE TABLE IF NOT EXISTS triples (key TEXT, source TEXT, relation TEXT, target TEXT, evidence TEXT,
                                    confidence REAL);
CREATE INDEX IF NOT EXISTS triples_key ON triples(key);
CREATE TABLE IF NOT EXISTS extracted (key TEXT PRIMARY KEY, at TEXT, model TEXT, triples INTEGER);
"""


def section_key(chunk: dict) -> str:
    return hashlib.sha1(f"{chunk['heading']}\0{chunk['text']}".encode()).hexdigest()[:16]


def stem(path: str) -> str:
    """The name a [[link]] uses for a note: its file name without .md / .txt, lower case."""
    name = path.rsplit("/", 1)[-1].strip().lower()
    return name.removesuffix(".md").removesuffix(".txt")


class WorkspaceGraph:
    def __init__(self, index):
        self.index = index
        with index.lock:
            index.conn.executescript(SCHEMA)

    # ---- reading the notes ----------------------------------------------------------------------
    def _sections(self, folder: str = "") -> list[dict]:
        return [{**c, "key": section_key(c)} for c in self.index.chunks(folder)]

    def pending(self, folder: str = "") -> list[dict]:
        """Sections under folder that the extractor has not read (new, or edited since)."""
        with self.index.lock:
            done = {r[0] for r in self.index.conn.execute("SELECT key FROM extracted")}
        seen, out = set(), []
        for s in self._sections(folder):
            if s["key"] not in done and s["key"] not in seen:     # a section copied into two notes is read once
                seen.add(s["key"])
                out.append(s)
        return out

    def build(self, folder: str = "", llm=None, budget_tokens: int | None = None, batch_chars: int = BATCH_CHARS,
              progress=lambda event: None, should_stop=lambda: False, model: str = "") -> dict:
        """Read the pending sections into triples, one batch (a few sections) per model call.
        Stops early - and says why - at the token budget, when should_stop() turns true, or when a call
        fails (a quota error repeats for every later call; unread sections stay pending for next time)."""
        todo = self.pending(folder)
        stats = {"done": 0, "total": len(todo), "tokens": 0, "triples": 0, "failed": 0, "stopped": None}
        batch, size = [], 0
        groups = []
        for s in todo:
            text = f"{s['heading']}\n{s['text']}" if s["heading"] else s["text"]
            if batch and size + len(text) > batch_chars:
                groups.append(batch)
                batch, size = [], 0
            batch.append({"id": s["key"], "text": text})
            size += len(text)
        if batch:
            groups.append(batch)
        for group in groups:
            if should_stop():
                stats["stopped"] = "stopped by the user"
                break
            if budget_tokens is not None and stats["tokens"] >= budget_tokens:
                stats["stopped"] = f"token budget of {budget_tokens:,} reached"
                break
            found, run = extract_paths_batch(group, llm=llm, max_chars=batch_chars)
            stats["tokens"] += run["tokens"]
            failed = set(run["failed_ids"])
            self._save({k: v for k, v in found.items() if k not in failed}, model)
            stats["done"] += len(group) - len(failed)
            stats["failed"] += len(failed)
            stats["triples"] += sum(len(v) for k, v in found.items() if k not in failed)
            progress({"type": "progress", **{k: stats[k] for k in ("done", "total", "tokens", "triples")}})
            if failed:
                stats["stopped"] = (run.get("error") or "the model call failed") + "; the rest stays pending"
                break
        return stats

    def _save(self, found: dict, model: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.index.lock:
            conn = self.index.conn
            for key, paths in found.items():
                conn.execute("DELETE FROM triples WHERE key = ?", (key,))
                conn.executemany("INSERT INTO triples VALUES (?, ?, ?, ?, ?, ?)",
                                 [(key, p["source"], p["relation"], p["target"], p.get("evidence", ""),
                                   p.get("confidence", 0.5)) for p in paths])
                conn.execute("INSERT OR REPLACE INTO extracted VALUES (?, ?, ?, ?)", (key, now, model, len(paths)))
            conn.commit()

    # ---- the graph ------------------------------------------------------------------------------
    def paths(self, folder: str = "") -> list[dict]:
        """Every stored triple whose section lives under folder, normalised (the course's entity
        normalisation) and tagged with the section it came from: source_chunk_id is the chunk id."""
        sections = defaultdict(list)
        for s in self._sections(folder):
            sections[s["key"]].append(s)
        if not sections:
            return []
        with self.index.lock:
            rows = list(self.index.conn.execute(
                "SELECT key, source, relation, target, evidence, confidence FROM triples"))
        out = []
        for key, source, relation, target, evidence, confidence in rows:
            for s in sections.get(key, ()):
                out.append({"source": source, "relation": relation, "target": target, "evidence": evidence,
                            "confidence": confidence, "source_chunk_id": s["id"], "path": s["path"],
                            "heading": s["heading"]})
        return normalize_entities(out)

    def links(self, folder: str = "") -> list[dict]:
        """[[links]] written in notes under folder, resolved to note paths anywhere in the workspace
        (a name shared by several notes resolves to the one nearest the linking note's folder)."""
        by_stem = defaultdict(list)
        with self.index.lock:
            for (path,) in self.index.conn.execute("SELECT path FROM files"):
                by_stem[stem(path)].append(path)
        out = []
        for s in self._sections(folder):
            for m in WIKILINK.finditer(s["text"]):
                name = m.group(1).strip()
                targets = by_stem.get(stem(name), [])
                if "/" in name:
                    want = name.lower().removesuffix(".md")
                    targets = [t for t in targets if t.lower().removesuffix(".md").endswith(want)] or targets
                if not targets:
                    continue
                here = s["path"].rsplit("/", 1)[0] if "/" in s["path"] else ""
                best = max(targets, key=lambda t: (t.startswith(here + "/") if here else "/" not in t, -len(t)))
                line = s["line"] + s["text"][:m.start()].count("\n")
                out.append({"source": s["path"], "target": best, "line": line, "text": _line_at(s["text"], m.start())})
        return out

    def backlinks(self, path: str) -> list[dict]:
        """Notes that link to `path`: {path, line, text}, the line around each [[link]]."""
        return [{"path": link["source"], "line": link["line"], "text": link["text"]}
                for link in self.links() if link["target"] == path and link["source"] != path]

    def stats(self, folder: str = "") -> dict:
        sections = self._sections(folder)
        with self.index.lock:
            done = {r[0] for r in self.index.conn.execute("SELECT key FROM extracted")}
        keys = {s["key"] for s in sections}
        return {"chunks": len(sections), "extracted": len(keys & done), "pending": len(keys - done)}

    def view(self, folder: str = "") -> dict:
        """Nodes and edges for the graph view: notes, the entities they mention, [[links]] between notes,
        relations between entities, and note -> entity edges for where each entity is mentioned."""
        paths, links = self.paths(folder), self.links(folder)
        notes = sorted({s["path"] for s in self._sections(folder)})
        nodes, edges, degree = {}, [], Counter()
        for p in notes:
            nodes[f"note:{p}"] = {"id": f"note:{p}", "label": stem(p), "kind": "note", "path": p}
        for link in links:
            if f"note:{link['target']}" not in nodes:           # a link out of the folder still shows its target
                nodes[f"note:{link['target']}"] = {"id": f"note:{link['target']}", "label": stem(link["target"]),
                                                   "kind": "note", "path": link["target"]}
            edges.append({"source": f"note:{link['source']}", "target": f"note:{link['target']}", "kind": "link"})
        mentions = set()
        for p in paths:
            for name in (p["source"], p["target"]):
                nodes.setdefault(f"entity:{name}", {"id": f"entity:{name}", "label": name, "kind": "entity"})
                mentions.add((p["path"], name))
            edges.append({"source": f"entity:{p['source']}", "target": f"entity:{p['target']}", "kind": "relation",
                          "label": p["relation"], "evidence": p["evidence"], "path": p["path"]})
        edges += [{"source": f"note:{path}", "target": f"entity:{name}", "kind": "mention"} for path, name in sorted(mentions)]
        # one edge per (source, target, kind, relation): the same [[link]] written twice, or the same fact
        # read from two sections, is one line in the picture (graph libraries reject exact duplicates)
        seen, unique = set(), []
        for e in edges:
            key = (e["source"], e["target"], e["kind"], e.get("label"))
            if key not in seen:
                seen.add(key)
                unique.append(e)
        edges = unique
        for e in edges:
            degree[e["source"]] += 1
            degree[e["target"]] += 1
        stats = {"notes": len(notes), "entities": sum(1 for n in nodes.values() if n["kind"] == "entity"),
                 "relations": len(paths), "links": len(links), **self.stats(folder)}
        return {"nodes": [{**n, "degree": degree[n["id"]]} for n in nodes.values()], "edges": edges, "stats": stats}

    def context(self, query: str, folder: str = "", hits=(), depth: int = 2, max_edges: int = 12) -> list[dict]:
        """Relations that bear on a question: seeded from the query's words and from the entities the
        best-matching sections mention, widened `depth` hops (the course's retrieve_subgraph), ranked so
        edges read out of the hit sections and edges with a seed come first. Each: the triple + path/heading."""
        paths = self.paths(folder)
        if not paths:
            return []
        hit_ids = {h["id"] for h in hits}
        seeds = sorted({name for p in paths if p["source_chunk_id"] in hit_ids for name in (p["source"], p["target"])})
        sub = retrieve_subgraph(build_graph(paths), query=query, seed_entities=seeds, depth=depth,
                                seeding_mode="hybrid", seed_match_mode="normalized")
        if not sub["edges"]:
            return []
        seed_keys = {normalize_entity_key(s) for s in seeds}
        where = {p["source_chunk_id"]: p for p in paths}
        ranked = sorted(sub["edges"], key=lambda e: (e["source_chunk_id"] not in hit_ids,
                                                     not ({normalize_entity_key(e["source"]),
                                                           normalize_entity_key(e["target"])} & seed_keys),
                                                     -float(e.get("confidence") or 0)))
        out, seen = [], set()
        for e in ranked:
            key = (e["source"], e["relation"], e["target"])
            if key in seen:
                continue
            seen.add(key)
            src = where.get(e["source_chunk_id"], {})
            out.append({**{k: e[k] for k in ("source", "relation", "target", "evidence")},
                        "path": src.get("path"), "heading": src.get("heading")})
            if len(out) >= max_edges:
                break
        return out


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else None].strip()[:200]
