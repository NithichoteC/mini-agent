"""Retrieval over documents: extract text (OCR only where a page has none), chunk, index, search.

    pages = extract(path, ocr=groq_ocr(cache_dir))     one dict per page: file, page, text, method
    chunks = chunk(pages)                              260 words, 40 overlap
    index = Index.build(chunks, embedder)              dense + BM25 + entity graph (+ page images)
    index.search(query, type="hybrid", k=5)            -> {"hits", "stages", "llm_tokens"}

The retrieval types share one set of indexes:
    dense           e5 vectors, exact inner product (FAISS IndexFlatIP)
    bm25            keywords alone (Okapi BM25, Lucene idf)
    hybrid          dense + BM25, fused by reciprocal rank (RRF)
    graph           dense seeds, widened by an entity co-occurrence graph, re-ranked by dense score
    agentic         a planner picks dense or hybrid and retries when the evidence looks weak
    corrective      a grader checks the top dense score; when weak, BM25 corrects the list
    multimodal      dense text + CLIP search over rendered page images
    agentic-llm     the model rewrites the query and decides whether keywords matter
    corrective-llm  the model grades the top passages; a weak set triggers a rewrite and a retry
`agentic` and `corrective` decide by score thresholds; the -llm types pass the decision to a model.

Heavy dependencies (requirements-rag.txt) are imported where they are used, so `import rag` and
the agent work without them.
"""
import base64
import hashlib
import json
import math
import pickle
import re
import time
import zlib
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

TEXT_MODEL = "intfloat/multilingual-e5-small"
CLIP_MODEL = "clip-ViT-B-32"
CHUNK_WORDS, CHUNK_OVERLAP = 260, 40
CANDIDATES = 25                         # each arm's list before fusion
RRF_K = 60
MIN_NATIVE_CHARS, MIN_ALNUM_RATIO = 100, 0.25
OCR_MAX_TOKENS = 1000                   # Groq free tier: 1,000 output tokens per minute on qwen3.8-27b
TYPES = ("dense", "bm25", "hybrid", "graph", "agentic", "corrective", "multimodal",
         "agentic-llm", "corrective-llm")

OCR_PROMPT = """Transcribe this document page faithfully for retrieval.
Preserve reading order, headings, lists, table rows, labels, numbers and named entities.
Write each table row on one line with cells separated by " | ".
For charts, maps, figures or diagrams, add a short [VISUAL DESCRIPTION] of what is shown.
Do not summarize or invent missing text. Return plain UTF-8 text only: no HTML, no code fences."""


# ---------- text ----------

def normalize(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


THAI = re.compile(r"[\u0e00-\u0e7f]")
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
SEGMENT_THAI = True     # False: the regex alone for every script (bench/rag compares the two)


def tokenize(text: str) -> list[str]:
    r"""Words for BM25, in matching form: lower case, Thai digits folded to 0-9.
    The regex \b\w\w+\b works for spaced scripts but breaks Thai twice over: Thai has no spaces
    between words, and Python counts Thai vowel and tone marks as non-word characters, so
    "คาดว่าจะขยายตัว" comes out as "คาดว", "าจะขยายต". Thai text goes through PyThaiNLP's newmm
    dictionary segmenter instead, when it is installed."""
    text = text.lower().translate(THAI_DIGITS)
    if SEGMENT_THAI and THAI.search(text):
        try:
            from pythainlp.tokenize import word_tokenize
        except ImportError:
            pass
        else:
            return [w for w in word_tokenize(text, engine="newmm", keep_whitespace=False)
                    if any(ch.isalnum() for ch in w)]
    return re.findall(r"(?u)\b\w\w+\b", text)


def entities(text: str) -> list[str]:
    """Capitalised phrases and acronyms - graph mechanics, not LLM-grade entity extraction."""
    return sorted(set(re.findall(r"\b(?:[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,3}|[A-Z]{2,})\b", text)))[:30]


def needs_ocr(text: str) -> bool:
    if len(text) < MIN_NATIVE_CHARS:
        return True
    return sum(ch.isalnum() for ch in text) / len(text) < MIN_ALNUM_RATIO


# ---------- extraction ----------

def groq_ocr(cache_dir=None, role: str = "ocr", dpi: int = 150, tiles: int = 1, split_on_cap: bool = True):
    """An ocr(png_bytes) function backed by the vision model behind `role` in runtime.yaml.
    Results are cached by the image and prompt, so a rerun never pays for the same page twice.
    `dpi`, `tiles` and `split_on_cap` tell ocr_page how to render: the model sees every image at one
    fixed token budget and may write at most OCR_MAX_TOKENS, so a dense page read whole can come back
    cut short or without its small print (a table's numbers), with no error. Strips get the full budget
    each, but on a light page a strip invites the model to repeat itself - so the default reads the
    page whole and splits it in two only when that read hits the output cap."""
    import llm_handler
    cache = Path(cache_dir) if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)

    def ocr(png: bytes) -> dict:
        key = hashlib.sha256(png + f"|{role}|{OCR_PROMPT}".encode()).hexdigest()
        hit = cache / f"ocr_{key[:32]}.json" if cache else None
        if hit and hit.exists():
            return {**json.loads(hit.read_text(encoding="utf-8")), "cached": True}
        image = "data:image/png;base64," + base64.b64encode(png).decode()
        started = time.perf_counter()
        r = llm_handler.call_llm([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": image}},
            {"type": "text", "text": OCR_PROMPT}]}], role=role, temperature=0, max_completion_tokens=OCR_MAX_TOKENS)
        if not r["ok"]:
            return {"ok": False, "error": r.get("error", "ocr failed")}
        usage = r.get("usage") or {}
        out = {"ok": True, "text": r["text"], "model": r["model"], "sec": time.perf_counter() - started,
               "server_sec": usage.get("total_time", 0.0),       # without rate-limit waits
               "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
        if hit:
            hit.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return {**out, "cached": False}

    ocr.dpi, ocr.tiles, ocr.split_on_cap = dpi, tiles, split_on_cap
    return ocr


def drop_repeats(text: str, min_len: int = 40) -> tuple[str, bool]:
    """Vision models sometimes loop: the page once, then the same sentences again until the token cap.
    A sentence of min_len+ characters that already appeared on the page is dropped; short ones (a table
    cell, "0.0 | 0.0") can repeat for real and are kept."""
    seen, out, looped = set(), [], False
    for line in text.splitlines():
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            key = " ".join(sentence.split()).lower()
            if len(key) >= min_len and key in seen:
                looped = True
                continue
            seen.add(key)
            kept.append(sentence)
        if kept or not line.strip():
            out.append(" ".join(kept).rstrip())
    return "\n".join(out), looped


def ocr_page(page, ocr) -> dict:
    """Read a PyMuPDF page in `ocr.tiles` strips; when a strip hits the output cap and
    `ocr.split_on_cap` is set, read it again in twice as many."""
    tiles = getattr(ocr, "tiles", 1)
    r = read_strips(page, ocr, tiles)
    if r["ok"] and r["truncated"] and getattr(ocr, "split_on_cap", False):
        again = read_strips(page, ocr, tiles * 2)
        if again["ok"]:
            for key in ("sec", "server_sec", "input_tokens", "output_tokens"):
                again[key] += r[key]                    # the first read was paid for too
            again["split"] = True
            return again
    return r


def read_strips(page, ocr, tiles: int) -> dict:
    """Render a PyMuPDF page in `tiles` horizontal strips (2% overlap), read each, join the text."""
    import pymupdf
    r = page.rect
    step, pad = r.height / tiles, r.height * 0.02
    total = {"ok": True, "text": [], "sec": 0.0, "server_sec": 0.0, "input_tokens": 0, "output_tokens": 0,
             "cached": True, "looped": False, "truncated": False, "split": False, "tiles": tiles}
    for n in range(tiles):
        clip = pymupdf.Rect(r.x0, max(r.y0, r.y0 + n * step - pad), r.x1, min(r.y1, r.y0 + (n + 1) * step + pad))
        res = ocr(page.get_pixmap(dpi=getattr(ocr, "dpi", 150), clip=clip).tobytes("png"))
        if not res["ok"]:
            return res
        text, looped = drop_repeats(res["text"])
        total["text"].append(text)
        total["cached"] &= res["cached"]
        total["looped"] |= looped
        total["truncated"] |= res.get("output_tokens", 0) >= OCR_MAX_TOKENS
        for key in ("sec", "server_sec", "input_tokens", "output_tokens"):
            total[key] += res.get(key, 0)
    total["text"] = "\n".join(total["text"])
    return total


def open_pdf(path):
    """Read the whole file first: PyMuPDF's many small reads are ~50x slower on a mounted
    Windows drive (WSL's /mnt) than one read of the bytes."""
    import pymupdf
    return pymupdf.open(stream=Path(path).read_bytes(), filetype="pdf")


def extract(path, ocr=None) -> list[dict]:
    """One row per page. PDFs use the text layer; a page with too little text goes to `ocr` when given.
    Any other file is read as UTF-8 text, one page."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return [{"file": path.name, "page": 1, "method": "text",
                 "text": normalize(path.read_text(encoding="utf-8", errors="replace"))}]
    rows = []
    with open_pdf(path) as doc:
        for number, page in enumerate(doc, 1):
            text = normalize(page.get_text())
            row = {"file": path.name, "page": number, "method": "native", "text": text}
            if needs_ocr(text):
                if ocr is None:
                    row["method"] = "native, needs OCR"
                else:
                    r = ocr_page(page, ocr)
                    if r["ok"]:
                        row.update(method="ocr", text=normalize(r["text"]), ocr_sec=r["sec"], cached=r["cached"],
                                   input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
                                   looped=r["looped"], truncated=r["truncated"])
                    else:
                        row["method"] = f"native, OCR failed: {r['error']}"
            rows.append(row)
    return rows


def headings(path) -> list[tuple[int, str]]:
    """(page, text) of a born-digital PDF's headings: lines set larger than the body text (bold lines
    too when large ones are few), without running headers or contents-page dot leaders. A heading
    wrapped over two lines comes back as one. Empty for anything that is not a PDF."""
    from collections import Counter
    if Path(path).suffix.lower() != ".pdf":
        return []
    lines = []
    with open_pdf(path) as doc:
        for number, page in enumerate(doc, 1):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if spans:
                        lines.append((number, " ".join(" ".join(s["text"] for s in spans).split()),
                                      round(max(s["size"] for s in spans), 1), all(s["flags"] & 16 for s in spans)))
    if not lines:
        return []
    body = Counter(round(size) for _, text, size, _ in lines for _ in text).most_common(1)[0][0]
    seen = Counter(text for _, text, _, _ in lines)

    def ok(text):
        return (2 <= len(text.split()) <= 14 and seen[text] <= 2 and not re.search(r"\.{4,}", text)
                and any(ch.isalpha() for ch in text))
    picked = [line for line in lines if line[2] >= body * 1.15 and ok(line[1])]
    if len(picked) < 6:
        picked += [line for line in lines if line[3] and line[2] >= body and ok(line[1])]
    out = []
    for number, text, size, _ in picked:
        last = out[-1] if out else None
        wraps = last and (last[1].endswith(("-", ",")) or text[:1].islower()
                          or last[1].rsplit(" ", 1)[-1] in ("and", "of", "the", "to", "for", "in", "a", "not", "with"))
        if wraps and last[0] == number and last[2] == size and len((last[1] + " " + text).split()) <= 18:
            joined = last[1][:-1] + text if last[1].endswith("-") else f"{last[1]} {text}"
            out[-1] = (number, joined, size)
        else:
            out.append((number, text, size))
    return [(number, text) for number, text, _ in out]


def chunk(pages, words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    chunks, step = [], max(1, words - overlap)
    for p in pages:
        ws = p["text"].split()
        for start in range(0, len(ws), step):
            chunks.append({"id": len(chunks), "file": p["file"], "page": p["page"],
                           "text": " ".join(ws[start:start + words]),
                           **({"ocr": True} if p.get("method") == "ocr" else {})})
            if start + words >= len(ws):
                break
    return chunks


# ---------- models ----------

_loaded = {}


class E5:
    """multilingual-e5: normalised vectors, with the "query: " / "passage: " prefixes it was trained on."""
    def __init__(self, name: str = TEXT_MODEL, device=None):
        from sentence_transformers import SentenceTransformer
        key = ("st", name, device)
        if key not in _loaded:
            _loaded[key] = SentenceTransformer(name, device=device)
        self.model, self.name = _loaded[key], name

    def encode(self, texts, kind: str = "passage"):
        prefix = "query: " if kind == "query" else "passage: "
        return self.model.encode([prefix + t for t in texts], batch_size=32, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False).astype("float32")


class Clip:
    """CLIP: page images and query text in one vector space."""
    def __init__(self, name: str = CLIP_MODEL, device=None):
        from sentence_transformers import SentenceTransformer
        key = ("st", name, device)
        if key not in _loaded:
            _loaded[key] = SentenceTransformer(name, device=device)
        self.model, self.name = _loaded[key], name

    def encode(self, items):
        return self.model.encode(items, normalize_embeddings=True, convert_to_numpy=True,
                                 show_progress_bar=False).astype("float32")


class HashEmbedder:
    """Offline stand-in for tests: hashed bag of words. Lexical, not semantic."""
    name = "hash"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def encode(self, texts, kind: str = "passage"):
        import numpy as np
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            for word in tokenize(text):
                out[i, zlib.crc32(word.encode()) % self.dim] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


# ---------- index ----------

def build_dense(chunks, embedder):
    import faiss
    vectors = embedder.encode([c["text"] for c in chunks], "passage")
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index, vectors


class BM25:
    """Okapi BM25 (k1 1.5, b 0.75) with Lucene's idf, log(1 + (N - n + 0.5) / (n + 0.5)), which is
    never negative. rank_bm25's BM25Okapi uses log((N - n + 0.5) / (n + 0.5)): a word found in more
    than half the passages then counts *against* a passage that contains it, which turns the ranking
    upside down in a small per-session index."""
    def __init__(self, docs, k1: float = 1.5, b: float = 0.75):
        self.n, self.k1, self.b = len(docs), k1, b
        self.lengths = [len(d) for d in docs]
        avg = sum(self.lengths) / max(1, self.n) or 1
        self.norm = [k1 * (1 - b + b * length / avg) for length in self.lengths]
        self.postings = defaultdict(list)            # word -> [(passage, term frequency)]
        for i, doc in enumerate(docs):
            counts = defaultdict(int)
            for word in doc:
                counts[word] += 1
            for word, tf in counts.items():
                self.postings[word].append((i, tf))
        self.idf = {w: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for w, p in self.postings.items()}

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.n
        for word in query:
            for i, tf in self.postings.get(word, ()):
                scores[i] += self.idf[word] * tf * (self.k1 + 1) / (tf + self.norm[i])
        return scores


def build_bm25(chunks):
    return BM25([tokenize(c["text"]) for c in chunks])


def build_graph(chunks) -> dict:
    """Chunk -> neighbouring chunks: consecutive mentions of the same entity are linked."""
    mentions = defaultdict(list)
    for c in chunks:
        for e in entities(c["text"]):
            mentions[e].append(c["id"])
    graph = defaultdict(set)
    for ids in mentions.values():
        ids = ids[:100]                     # a generic capitalised word would otherwise link everything
        for a, b in zip(ids, ids[1:]):
            graph[a].add(b)
            graph[b].add(a)
    return dict(graph)


def build_visual(pdfs, clip, dpi: int = 120):
    """One CLIP vector per rendered PDF page."""
    import faiss
    import io
    import numpy as np
    from PIL import Image
    vectors, keys = [], []
    for pdf in pdfs:
        with open_pdf(pdf) as doc:
            for number, page in enumerate(doc, 1):
                with Image.open(io.BytesIO(page.get_pixmap(dpi=dpi).tobytes("png"))) as im:
                    vectors.append(clip.encode([im.convert("RGB")])[0])
                keys.append((Path(pdf).name, number))
    vectors = np.asarray(vectors, dtype="float32")
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index, keys


class Stages:
    """Milliseconds per stage of one query, and the tokens any model calls used."""
    def __init__(self):
        self.ms, self.tokens = defaultdict(float), 0

    @contextmanager
    def __call__(self, name):
        started = time.perf_counter_ns()
        try:
            yield
        finally:
            self.ms[name] += (time.perf_counter_ns() - started) / 1e6


def rrf(lists, k: int, constant: int = RRF_K) -> list[int]:
    scores = defaultdict(float)
    for ranked in lists:
        for rank, item in enumerate(ranked, 1):
            scores[int(item)] += 1 / (constant + rank)
    return [i for i, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]]


class Index:
    def __init__(self, chunks, embedder, dense, vectors, bm25, graph, clip=None, visual=None, visual_keys=()):
        self.chunks, self.embedder = chunks, embedder
        self.dense, self.vectors, self.bm25, self.graph = dense, vectors, bm25, graph
        self.clip, self.visual, self.visual_keys = clip, visual, list(visual_keys)
        self.first_chunk = {}
        for c in chunks:
            self.first_chunk.setdefault((c["file"], c["page"]), c["id"])

    @classmethod
    def build(cls, chunks, embedder, clip=None, pdfs=()):
        dense, vectors = build_dense(chunks, embedder)
        visual, keys = build_visual(pdfs, clip) if clip and pdfs else (None, ())
        return cls(chunks, embedder, dense, vectors, build_bm25(chunks), build_graph(chunks), clip, visual, keys)

    def sizes(self) -> dict:
        """Serialized bytes per structure - what it costs to keep on disk."""
        import faiss
        out = {"dense": len(faiss.serialize_index(self.dense)),
               "bm25": len(pickle.dumps(self.bm25, protocol=pickle.HIGHEST_PROTOCOL)),
               "graph": len(pickle.dumps(self.graph, protocol=pickle.HIGHEST_PROTOCOL))}
        if self.visual is not None:
            out["visual"] = len(faiss.serialize_index(self.visual))
        return out

    def save(self, folder):
        import numpy as np
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chunks.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in self.chunks), encoding="utf-8")
        np.save(folder / "dense.npy", self.vectors)
        (folder / "meta.json").write_text(json.dumps({"embedder": self.embedder.name, "chunks": len(self.chunks)}))

    @classmethod
    def load(cls, folder, embedder):
        """BM25 and the graph are rebuilt: both take well under a second at session scale."""
        import faiss
        import numpy as np
        folder = Path(folder)
        meta = json.loads((folder / "meta.json").read_text())
        if meta["embedder"] != embedder.name:
            raise ValueError(f"index was built with {meta['embedder']}, not {embedder.name}")
        chunks = [json.loads(line) for line in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        vectors = np.load(folder / "dense.npy")
        dense = faiss.IndexFlatIP(vectors.shape[1])
        dense.add(vectors)
        return cls(chunks, embedder, dense, vectors, build_bm25(chunks), build_graph(chunks))

    # ---------- arms ----------

    def _encode(self, query):
        return self.embedder.encode([query], "query")

    def _dense(self, qv, k=CANDIDATES):
        scores, ids = self.dense.search(qv, min(k, self.dense.ntotal))
        return [i for i in ids[0].tolist() if i >= 0], scores[0].tolist()

    def _bm25(self, query, k=CANDIDATES):
        import numpy as np
        scores = np.asarray(self.bm25.get_scores(tokenize(query)))
        k = min(k, len(scores))
        ids = np.argpartition(scores, -k)[-k:]
        return ids[np.argsort(-scores[ids])].tolist()

    # ---------- the types ----------

    def search(self, query: str, type: str = "hybrid", k: int = 5, llm=None) -> dict:
        if type not in TYPES:
            raise ValueError(f"unknown retrieval type '{type}' (have: {', '.join(TYPES)})")
        if type.endswith("-llm") and llm is None:
            raise ValueError(f"'{type}' needs llm=")
        t = Stages()
        ids = getattr(self, "_t_" + type.replace("-", "_"))(query, k, t, llm)
        return {"hits": [self.chunks[i] for i in ids[:k]], "stages": dict(t.ms), "llm_tokens": t.tokens}

    def _t_dense(self, q, k, t, llm):
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            return self._dense(qv, k)[0]

    def _t_bm25(self, q, k, t, llm):
        with t("bm25"):
            return self._bm25(q, k)

    def _t_hybrid(self, q, k, t, llm):
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            dense_ids, _ = self._dense(qv)
        with t("bm25"):
            sparse_ids = self._bm25(q)
        with t("fusion"):
            return rrf([dense_ids, sparse_ids], k)

    def _t_graph(self, q, k, t, llm):
        import numpy as np
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            seeds, _ = self._dense(qv, max(k, 8))
        with t("graph_expand"):
            ids = set(seeds)
            for s in seeds:
                ids.update(self.graph.get(s, ()))
            ids = np.fromiter(ids, dtype=np.int64)
        with t("rerank"):
            return ids[np.argsort(-(self.vectors[ids] @ qv[0]))][:k].tolist()

    def _t_agentic(self, q, k, t, llm):
        with t("plan"):
            route = "hybrid" if len(tokenize(q)) > 5 else "dense"
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            dense_ids, scores = self._dense(qv)
        lists = [dense_ids]
        if route == "hybrid":
            with t("bm25"):
                lists.append(self._bm25(q))
        with t("fusion"):
            result = rrf(lists, k)
        if not (scores and scores[0] >= 0.72):          # weak evidence: one retry with a widened query
            with t("retry_encode"):
                retry_qv = self._encode(q + " evidence source")
            with t("retry_dense"):
                retry_ids, _ = self._dense(retry_qv)
            with t("retry_fusion"):
                result = rrf([result, retry_ids], k)
        return result

    def _t_corrective(self, q, k, t, llm):
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            dense_ids, scores = self._dense(qv)
        with t("grade"):
            correct = bool(scores) and scores[0] >= 0.75
        if correct:
            return dense_ids[:k]
        with t("bm25"):
            sparse_ids = self._bm25(q)
        with t("refine"):
            return rrf([dense_ids, sparse_ids], k)

    def _t_multimodal(self, q, k, t, llm):
        if self.visual is None:
            raise ValueError("multimodal needs a visual index (build with clip= and pdfs=)")
        with t("text_encode"):
            qv = self._encode(q)
        with t("dense"):
            dense_ids, _ = self._dense(qv)
        with t("clip_encode"):
            cv = self.clip.encode([q])
        with t("visual"):
            _, pages = self.visual.search(cv, min(CANDIDATES, self.visual.ntotal))
            visual_ids = [self.first_chunk[self.visual_keys[i]] for i in pages[0]
                          if self.visual_keys[i] in self.first_chunk]
        with t("fusion"):
            return rrf([dense_ids, visual_ids], k)

    def _t_agentic_llm(self, q, k, t, llm):
        with t("llm_plan"):
            plan = ask_json(llm, t, PLAN_PROMPT.format(question=q))
        query = str(plan.get("query") or q)
        with t("text_encode"):
            qv = self._encode(query)
        with t("dense"):
            lists = [self._dense(qv)[0]]
        if plan.get("keywords", True):
            with t("bm25"):
                lists.append(self._bm25(f"{q} {query}"))
        with t("fusion"):
            return rrf(lists, k)

    def _t_corrective_llm(self, q, k, t, llm):
        ids = self._t_dense(q, CANDIDATES, t, llm)
        with t("llm_grade"):
            relevant = self._grade(q, ids[:k], llm, t)
        if len(relevant) >= min(2, k):
            return relevant + [i for i in ids if i not in relevant]
        with t("llm_rewrite"):
            query = str(ask_json(llm, t, PLAN_PROMPT.format(question=q)).get("query") or q)
        with t("text_encode"):
            qv = self._encode(query)
        with t("dense"):
            retry = self._dense(qv)[0]
        with t("bm25"):
            sparse = self._bm25(f"{q} {query}")
        with t("refine"):
            fused = rrf([retry, sparse], CANDIDATES)
        with t("llm_grade"):
            relevant += [i for i in self._grade(q, [i for i in fused if i not in relevant][:k], llm, t)]
        return relevant + [i for i in fused if i not in relevant]

    def _grade(self, q, ids, llm, t) -> list[int]:
        passages = "\n\n".join(f"[{n}] {self.chunks[i]['text'][:700]}" for n, i in enumerate(ids, 1))
        verdict = ask_json(llm, t, GRADE_PROMPT.format(question=q, passages=passages))
        keep = {int(n) for n in verdict.get("relevant", []) if str(n).isdigit()}
        return [i for n, i in enumerate(ids, 1) if n in keep]


# ---------- model-driven planning and grading ----------

PLAN_PROMPT = """You prepare a search over a document collection.
Question: {question}
Reply with JSON only: {{"query": "<the question rewritten as a short search query>",
"keywords": <true if exact names, numbers or rare terms matter, else false>}}"""

GRADE_PROMPT = """Which passages contain information that helps answer the question?
Question: {question}

{passages}

Reply with JSON only: {{"relevant": [<numbers of the helpful passages>]}}"""


def ask_json(llm, t: Stages, prompt: str) -> dict:
    """One model call that should return a JSON object; anything unreadable counts as {}."""
    r = llm(prompt)
    t.tokens += (r.get("usage") or {}).get("total_tokens", 0)
    match = re.search(r"\{.*\}", r.get("text") or "", re.S) if r.get("ok") else None
    try:
        out = json.loads(match.group(0)) if match else {}
    except ValueError:
        out = {}
    return out if isinstance(out, dict) else {}


def model_llm(role: str = "actor"):
    """An llm(prompt) function for the -llm types, through llm_handler and runtime.yaml."""
    import llm_handler
    return lambda prompt: llm_handler.call_llm([{"role": "user", "content": prompt}], role=role,
                                               temperature=0, max_completion_tokens=400)
