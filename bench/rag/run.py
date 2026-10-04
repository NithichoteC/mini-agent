"""RAG benchmark: what each retrieval type costs, and how often it finds the right page.

    python bench/rag/run.py ocr         OCR accuracy: pages rendered to images, read back, compared
                                        with their own text layer
    python bench/rag/run.py cost        the instructor's measurements: ingestion per MB, index size,
                                        GB/TB projection, query latency per type
    python bench/rag/run.py quality     hit@5 / MRR@10 per type on questions_en.jsonl
    python bench/rag/run.py thai        Thai: the instructor's regex tokenizer vs PyThaiNLP segmentation
                                        on questions_th.jsonl, and OCR accuracy on Thai pages
    python bench/rag/run.py answer      end to end: the actor answers from the top-5 hybrid passages,
                                        or from the whole document when it is small; the reviewer judges

Needs requirements-rag.txt and GROQ_API_KEY (OCR and the -llm types). Results land in results/.
Model calls are cached in cache/, so a rerun repeats the timing work but not the paid work.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE.parent.parent / ".env")
import rag  # noqa: E402

CORPUS, CACHE, RESULTS = HERE / "corpus", HERE / "cache", HERE / "results"
FILES = {   # name -> (url or None, sha256); corpus.md says the same for people
    "aura.pdf": (None, "9279474636d7e9501d3a93db69744954fccbd6dcfc070c473a002255fc59cdd4"),
    "tem-2025-02.pdf": ("https://documents1.worldbank.org/curated/en/099021125051038392/pdf/"
                        "P5080791f9e0bc03b1ab8019753dc6998d3.pdf",
                        "4c308c3e606490ab3b4d02ddfd717ca47498b947e4470c902fe1487ee855ee63"),
    "tem-2023-12.pdf": ("https://documents1.worldbank.org/curated/en/099121223123018912/pdf/"
                        "P5010091ef52cc09d1b46c1af1a43820def.pdf",
                        "f7af28e3c576088c0799a95d8ab29efd4e36a6a48a855b5247e987a4db46a544"),
    "monthly-2026-03.pdf": ("https://documents1.worldbank.org/curated/en/099923303202623923/pdf/"
                            "IDU-61588b11-5f8e-40ac-9320-840ae8597ecc.pdf",
                            "a7166abb8c5070fa258c906dba936eb3c63118dc0404eb5ffe5babf07e3306db"),
}
FILES_TH = {
    "budget-2569-statement.pdf": ("https://www.bb.go.th/download.php?id=31908",
                                  "fa77339c3d5bd1043984099449f2e3ddfada957ab95438181a669b22773249cd"),
}
# Born-digital pages rendered to images: OCR reads them, their own text layer is the answer key.
# Prose, prose, a page of small tables, a full-page table, a table with a chart, a dense table.
SCANNED = [("aura.pdf", 2), ("tem-2025-02.pdf", 12), ("tem-2025-02.pdf", 38),
           ("tem-2025-02.pdf", 51), ("tem-2023-12.pdf", 23), ("monthly-2026-03.pdf", 4)]
SCAN_DPI = 150


def corpus(files=FILES) -> list[Path]:
    import requests
    CORPUS.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, (url, sha) in files.items():
        path = CORPUS / name
        if not path.exists():
            if url is None:
                sys.exit(f"{name} is course material: copy it into {CORPUS}")
            print(f"downloading {name}", file=sys.stderr)
            path.write_bytes(requests.get(url, timeout=120).content)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            sys.exit(f"{name}: sha256 does not match corpus.md")
        paths.append(path)
    return paths


def save(name: str, rows, note: str = ""):
    """rows: list of dicts -> results/<name>.csv"""
    import csv
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> results/{name}.csv {note}", file=sys.stderr)


def table(rows, cols=None) -> str:
    cols = cols or list(rows[0])
    fmt = lambda v: f"{v:,.3f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    return "\n".join(lines + ["| " + " | ".join(fmt(r[c]) for c in cols) + " |" for r in rows])


def environment() -> dict:
    import torch
    return {"python": platform.python_version(), "platform": platform.platform(terse=True),
            "cpu_threads": os.cpu_count(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}


# ---------- OCR accuracy ----------

def numbers(text: str) -> list[str]:
    """Every number on a page (Thai digits folded, thousands separators dropped): 1,475.0 -> 1475.0."""
    import re
    return [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text.translate(rag.THAI_DIGITS))]


def number_recall(truth: str, read: str) -> float:
    """Share of the page's numbers that the OCR reproduced exactly - the figure a budget reader needs.
    Word recall can stay high while every amount on the page is wrong."""
    from collections import Counter
    want, got = Counter(numbers(truth)), Counter(numbers(read))
    return sum((want & got).values()) / max(1, sum(want.values()))


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cmd_ocr(args):
    """CER is order-sensitive (a table read row by row vs column by column counts as errors);
    word recall is not, so the two together separate misread characters from reading order."""
    import pymupdf
    from collections import Counter
    corpus()
    rows = []
    for mode, tiles, split in (("1 strip", 1, False), ("2 strips", 2, False), ("split on cap", 1, True)):
        ocr = rag.groq_ocr(CACHE / "ocr", dpi=SCAN_DPI, tiles=tiles, split_on_cap=split)
        for name, number in SCANNED:
            with pymupdf.open(CORPUS / name) as doc:
                page = doc[number - 1]
                truth = " ".join(page.get_text().split())
                r = rag.ocr_page(page, ocr)
            if not r["ok"]:
                sys.exit(f"OCR failed on {name} p{number}: {r['error']}")
            read = " ".join(r["text"].replace("[VISUAL DESCRIPTION]", " ").replace(" | ", " ").split())
            want, got = Counter(truth.lower().split()), Counter(read.lower().split())
            digits = sum(c.isdigit() for c in truth)
            rows.append({"mode": mode, "file": name, "page": number, "strips": r["tiles"], "truth_chars": len(truth),
                         "ocr_chars": len(read), "cer": edit_distance(truth, read) / max(1, len(truth)),
                         "word_recall": sum((want & got).values()) / max(1, sum(want.values())),
                         "digit_ratio": sum(c.isdigit() for c in read) / digits if digits else 1.0,
                         "number_recall": number_recall(truth, read),
                         "looped": r["looped"], "truncated": r["truncated"],
                         "server_sec": round(r["server_sec"], 2), "input_tokens": r["input_tokens"],
                         "output_tokens": r["output_tokens"]})
            print(f"{mode} {name} p{number}: cer {rows[-1]['cer']:.3f} "
                  f"recall {rows[-1]['word_recall']:.3f} digits {rows[-1]['digit_ratio']:.2f}", file=sys.stderr)
    save("ocr_accuracy", rows, f"({SCAN_DPI} dpi)")
    print(table(rows))
    summary = []
    for mode in dict.fromkeys(r["mode"] for r in rows):
        group = [r for r in rows if r["mode"] == mode]
        n = len(group)
        summary.append({"mode": mode, "mean_cer": sum(r["cer"] for r in group) / n,
                        "mean_word_recall": sum(r["word_recall"] for r in group) / n,
                        "min_word_recall": min(r["word_recall"] for r in group),
                        "pages_looped": sum(r["looped"] for r in group),
                        "input_tokens": sum(r["input_tokens"] for r in group),
                        "output_tokens": sum(r["output_tokens"] for r in group)})
    save("ocr_summary", summary)
    print(table(summary))


# ---------- A: cost and speed (the instructor's measurements) ----------

QUERIES = [   # the instructor's four, so the latency table compares with his
    "What are the document's central claims and supporting evidence?",
    "Which institutions, organizations, or people are discussed?",
    "What risks, trends, and policy implications appear in the documents?",
    "Find information represented in a chart, table, map, or diagram.",
]
RULE_TYPES = ("dense", "bm25", "hybrid", "graph", "agentic", "corrective", "multimodal")


def timed(fn, repeats):
    out, secs = None, []
    for _ in range(repeats):
        started = time.perf_counter()
        out = fn()
        secs.append(time.perf_counter() - started)
    return out, secs


def mean_sd(xs):
    m = sum(xs) / len(xs)
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0)


def ingest(paths):
    """Extract every page (OCR where the rule says so), cached in cache/pages.jsonl with its timings."""
    ocr = rag.groq_ocr(CACHE / "ocr")
    started = time.perf_counter()
    pages = [row for path in paths for row in rag.extract(path, ocr=ocr)]
    wall = time.perf_counter() - started
    return pages, wall


def cmd_cost(args):
    import torch
    paths = corpus()
    total_mb = sum(p.stat().st_size for p in paths) / 1e6
    env = environment()
    print(env, file=sys.stderr)

    pages, extract_wall = ingest(paths)
    ocr_rows = [p for p in pages if p["method"] == "ocr"]
    ocr_server = sum(p.get("ocr_sec", 0) for p in ocr_rows)
    # a cached OCR page costs no time now; add back what the first, paid read took
    extract_sec = extract_wall + sum(p["ocr_sec"] for p in ocr_rows if p.get("cached"))
    chunks, chunk_secs = timed(lambda: rag.chunk(pages), args.repeats)

    stages = [{"stage": "extract text + selective OCR", "device": "-", "mean_sec": extract_sec, "sd_sec": 0.0},
              {"stage": "  of which OCR model calls", "device": "groq", "mean_sec": ocr_server, "sd_sec": 0.0},
              {"stage": "chunking", "device": "cpu", **dict(zip(("mean_sec", "sd_sec"), mean_sd(chunk_secs)))}]
    devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
    dense = {}
    for device in devices:
        emb = rag.E5(device=device)
        emb.encode(["warm up"], "query")                 # model load and first call are not ingestion
        (index, _), secs = timed(lambda: rag.build_dense(chunks, emb), args.repeats)
        dense[device] = mean_sd(secs)
        stages.append({"stage": "dense embed + FAISS", "device": device,
                       **dict(zip(("mean_sec", "sd_sec"), dense[device]))})
    bm25, secs = timed(lambda: rag.build_bm25(chunks), args.repeats)
    stages.append({"stage": "BM25", "device": "cpu", **dict(zip(("mean_sec", "sd_sec"), mean_sd(secs)))})
    graph, secs = timed(lambda: rag.build_graph(chunks), args.repeats)
    stages.append({"stage": "entity graph", "device": "cpu", **dict(zip(("mean_sec", "sd_sec"), mean_sd(secs)))})
    clip = rag.Clip(device=devices[0])
    clip.encode(["warm up"])
    (visual, keys), secs = timed(lambda: rag.build_visual(paths, clip), args.repeats)
    stages.append({"stage": "render pages + CLIP + FAISS", "device": devices[0],
                   **dict(zip(("mean_sec", "sd_sec"), mean_sd(secs)))})
    emb = rag.E5(device=devices[0])
    index = rag.Index(chunks, emb, *rag.build_dense(chunks, emb), bm25, graph, clip, visual, keys)
    save("cost_stages", stages)

    sec = {r["stage"]: r["mean_sec"] for r in stages if r["device"] in ("-", "cpu", devices[0])}
    core = extract_sec + sec["chunking"]
    sizes = index.sizes()
    dense_sec = dense[devices[0]][0]
    extra = {"dense": (0.0, 0), "bm25": (sec["BM25"], sizes["bm25"]), "hybrid": (sec["BM25"], sizes["bm25"]),
             "graph": (sec["entity graph"], sizes["graph"]), "agentic": (sec["BM25"], sizes["bm25"]),
             "corrective": (sec["BM25"], sizes["bm25"]),
             "multimodal": (sec["render pages + CLIP + FAISS"], sizes["visual"])}
    ocr_in = sum(p.get("input_tokens", 0) for p in ocr_rows)
    ocr_out = sum(p.get("output_tokens", 0) for p in ocr_rows)
    per_type = []
    for t, (extra_sec, extra_bytes) in extra.items():
        base = 0.0 if t == "bm25" else dense_sec
        base_bytes = 0 if t == "bm25" else sizes["dense"]
        ingestion = core + base + extra_sec
        index_mb = (base_bytes + extra_bytes) / 1e6
        per_type.append({"type": t, "ingestion_sec": ingestion, "sec_per_MB": ingestion / total_mb,
                         "index_MB": index_mb, "index_MB_per_input_MB": index_mb / total_mb,
                         "ocr_tokens_per_MB": (ocr_in + ocr_out) / total_mb})
    save("cost_per_type", per_type)

    projection = [{"type": r["type"], "scale": name, "hours": r["sec_per_MB"] * mb / 3600,
                   "index_GB": r["index_MB_per_input_MB"] * mb / 1000}
                  for r in per_type for name, mb in (("1 GB", 1_000), ("1 TB", 1_000_000))]
    save("cost_projection", projection)

    # query latency: warm-up, then shuffled query and type order on every repeat
    rng = random.Random(42)
    for q in QUERIES[:2]:
        for t in RULE_TYPES:
            index.search(q, t)
    latency = []
    for repeat in range(args.query_repeats):
        for qi in rng.sample(range(len(QUERIES)), len(QUERIES)):
            for t in rng.sample(RULE_TYPES, len(RULE_TYPES)):
                started = time.perf_counter_ns()
                index.search(QUERIES[qi], t)
                latency.append({"type": t, "query": qi, "ms": (time.perf_counter_ns() - started) / 1e6})
    save("query_latency_raw", latency)
    summary = []
    for t in RULE_TYPES:
        xs = sorted(r["ms"] for r in latency if r["type"] == t)
        m, sd = mean_sd(xs)
        summary.append({"type": t, "calls": len(xs), "mean_ms": m, "median_ms": xs[len(xs) // 2],
                        "p95_ms": xs[int(0.95 * (len(xs) - 1))], "ci95_ms": 1.96 * sd / math.sqrt(len(xs))})
    save("query_latency", summary)

    facts = {**env, "input_MB": round(total_mb, 3), "pages": len(pages), "chunks": len(chunks),
             "ocr_pages": len(ocr_rows), "ocr_input_tokens": ocr_in, "ocr_output_tokens": ocr_out,
             "repeats": args.repeats, "query_repeats": args.query_repeats}
    (RESULTS / "cost_facts.json").write_text(json.dumps(facts, indent=2))
    print(json.dumps(facts, indent=2))
    for rows in (stages, per_type, summary):
        print(table(rows) + "\n")


# ---------- B: retrieval quality ----------

def questions(path=HERE / "questions_en.jsonl"):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score(hits, gold) -> dict:
    """Page-level: a passage counts when its (file, page) is a gold page."""
    gold = {tuple(g) for g in gold}
    pages = [(h["file"], h["page"]) for h in hits]
    first = next((i for i, p in enumerate(pages[:10], 1) if p in gold), None)
    return {"hit5": int(first is not None and first <= 5), "rr10": 1 / first if first else 0.0,
            "gold_found5": len(gold & set(pages[:5])) / len(gold)}


def build_index(paths, visual=True):
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pages, _ = ingest(paths)
    chunks = rag.chunk(pages)
    clip = rag.Clip(device=device) if visual else None
    return rag.Index.build(chunks, rag.E5(device=device), clip=clip, pdfs=paths if visual else ())


def summarize(rows, by):
    out = []
    for key in dict.fromkeys(r[by] for r in rows):
        group = [r for r in rows if r[by] == key]
        for t in dict.fromkeys(r["type"] for r in group):
            g = [r for r in group if r["type"] == t]
            out.append({by: key, "type": t, "n": len(g), "hit@5": sum(r["hit5"] for r in g) / len(g),
                        "MRR@10": sum(r["rr10"] for r in g) / len(g),
                        "gold_pages@5": sum(r["gold_found5"] for r in g) / len(g)})
    return out


def diagnostics(index) -> dict:
    """Why the threshold types behave as they do: the top dense score of every question, answerable
    or not, against the thresholds the agentic (0.72) and corrective (0.75) rules use."""
    def top(q):
        return index._dense(index._encode(q["question"]), 1)[1][0]
    qs = questions()
    answerable = sorted(top(q) for q in qs if q["gold"])
    unanswerable = sorted(top(q) for q in qs if not q["gold"])
    graph_same = sum([h["id"] for h in index.search(q["question"], "graph", 5)["hits"]]
                     == [h["id"] for h in index.search(q["question"], "dense", 5)["hits"]] for q in qs)
    return {"top_score_answerable": [round(answerable[0], 3), round(answerable[len(answerable) // 2], 3),
                                     round(answerable[-1], 3)],
            "top_score_unanswerable": [round(x, 3) for x in unanswerable],
            "below_0.72": sum(x < 0.72 for x in answerable + unanswerable),
            "below_0.75": sum(x < 0.75 for x in answerable + unanswerable),
            "graph_top5_equals_dense": f"{graph_same}/{len(qs)}",
            "note": "top_score_answerable is [min, median, max]"}


def cmd_quality(args):
    paths = corpus()
    qs = [q for q in questions() if q["gold"]]              # "none" questions are for answer tests
    index = build_index(paths, visual="multimodal" in args.types)
    llm = rag.model_llm(args.llm_role) if any(t.endswith("-llm") for t in args.types) else None
    rows = []
    for q in qs:
        for t in args.types:
            started = time.perf_counter()
            r = index.search(q["question"], t, k=10, llm=llm)
            rows.append({"id": q["id"], "category": q["category"], "style": q["style"], "type": t,
                         **score(r["hits"], q["gold"]), "ms": (time.perf_counter() - started) * 1000,
                         "llm_tokens": r["llm_tokens"],
                         "top5": " ".join(f"{h['file'][:-4]}#{h['page']}" for h in r["hits"][:5])})
    name = "quality" + ("_llm" if llm else "")
    save(f"{name}_raw", rows)
    overall = [{k: v for k, v in r.items() if k != "all"} for r in summarize([{**r, "all": "all"} for r in rows], "all")]
    for r in overall:
        g = [x for x in rows if x["type"] == r["type"]]
        r["mean_ms"] = sum(x["ms"] for x in g) / len(g)
        r["llm_tokens_per_q"] = sum(x["llm_tokens"] for x in g) / len(g)
    save(name, overall)
    save(f"{name}_by_category", summarize(rows, "category"))
    save(f"{name}_by_style", summarize(rows, "style"))
    print(table(overall, ["type", "n", "hit@5", "MRR@10", "gold_pages@5", "mean_ms", "llm_tokens_per_q"]))
    if not llm:
        (RESULTS / "quality_diagnostics.json").write_text(json.dumps(diagnostics(index), indent=2))
    by_style = summarize(rows, "style")
    print("\n" + table(by_style, ["style", "type", "n", "hit@5", "MRR@10"]))
    print("\n" + table(summarize(rows, "category"), ["category", "type", "n", "hit@5", "MRR@10"]))


# ---------- Thai ----------

THAI_SCANNED = [2, 9, 26]     # prose, prose with figures, a page of budget totals


def cmd_thai(args):
    import torch
    from collections import Counter
    (path,) = corpus(FILES_TH)
    pages = rag.extract(path)
    chunks = rag.chunk(pages)
    qs = [q for q in questions(HERE / "questions_th.jsonl") if q["gold"]]
    emb = rag.E5(device="cuda" if torch.cuda.is_available() else "cpu")
    rows, tokens = [], []
    for segment in (False, True):
        rag.SEGMENT_THAI = segment
        name = "pythainlp newmm" if segment else "regex \\b\\w\\w+\\b"
        sample = [rag.tokenize(c["text"]) for c in chunks]
        tokens.append({"tokenizer": name, "tokens_per_chunk": sum(map(len, sample)) / len(sample),
                       "distinct_tokens": len({w for doc in sample for w in doc}),
                       "example": " / ".join(rag.tokenize("เศรษฐกิจในปี 2569 คาดว่าจะขยายตัว"))})
        index = rag.Index.build(chunks, emb)
        for q in qs:
            for t in ("dense", "bm25", "hybrid"):
                r = index.search(q["question"], t, k=10)
                rows.append({"tokenizer": name, "id": q["id"], "category": q["category"], "style": q["style"],
                             "type": t, **score(r["hits"], q["gold"])})
    rag.SEGMENT_THAI = True
    save("thai_tokens", tokens)
    save("thai_quality_raw", rows)
    summary = []
    for name in dict.fromkeys(r["tokenizer"] for r in rows):
        for t in ("dense", "bm25", "hybrid"):
            for style in ("all", "keyword", "paraphrase"):
                g = [r for r in rows if r["tokenizer"] == name and r["type"] == t and style in ("all", r["style"])]
                summary.append({"tokenizer": name, "type": t, "style": style, "n": len(g),
                                "hit@5": sum(r["hit5"] for r in g) / len(g), "MRR@10": sum(r["rr10"] for r in g) / len(g)})
    save("thai_quality", summary)
    print(table(tokens) + "\n\n" + table(summary))
    if args.no_ocr:
        return
    ocr = rag.groq_ocr(CACHE / "ocr", dpi=SCAN_DPI)
    ocr_rows = []
    with rag.open_pdf(path) as doc:
        for number in THAI_SCANNED:
            page = doc[number - 1]
            truth = " ".join(page.get_text().split()).translate(rag.THAI_DIGITS)
            r = rag.ocr_page(page, ocr)
            if not r["ok"]:
                sys.exit(f"OCR failed on p{number}: {r['error']}")
            read = " ".join(r["text"].replace("[VISUAL DESCRIPTION]", " ").replace(" | ", " ").split()).translate(rag.THAI_DIGITS)
            want, got = Counter(rag.tokenize(truth)), Counter(rag.tokenize(read))
            ocr_rows.append({"page": number, "truth_chars": len(truth), "ocr_chars": len(read),
                             "cer": edit_distance(truth, read) / max(1, len(truth)),
                             "word_recall": sum((want & got).values()) / max(1, sum(want.values())),
                             "numbers": len(numbers(truth)), "number_recall": number_recall(truth, read),
                             "strips": r["tiles"], "looped": r["looped"], "input_tokens": r["input_tokens"],
                             "output_tokens": r["output_tokens"]})
            print(f"thai p{number}: cer {ocr_rows[-1]['cer']:.3f} recall {ocr_rows[-1]['word_recall']:.3f}", file=sys.stderr)
    save("thai_ocr", ocr_rows)
    print("\n" + table(ocr_rows))


# ---------- B4: end to end ----------

ANSWER_PROMPT = """Answer the question using only the document text below.
If the text does not contain the answer, reply exactly: not in the documents.
Be brief and cite the source as (file p.N).

Question: {question}

{context}"""

JUDGE_PROMPT = """You grade an answer against a reference.
Question: {question}
Reference answer: {reference}
Answer to grade: {answer}

Reply with one word on the first line:
CORRECT  the answer states the reference's key facts (extra detail is fine)
PARTIAL  some of the key facts, or right but vague
WRONG    the key facts are missing or contradicted
REFUSED  the answer says the information is not in the documents
Then one short line of reason."""

SMALL_DOCS = ("aura.pdf", "monthly-2026-03.pdf")     # whole text fits the prompt (<= ~6k tokens)


def ask(role, prompt, max_tokens=500):
    """One cached model call. A failed call stops the run (progress is kept) instead of being
    scored as an answer - the free tier's daily token cap ends a run part-way."""
    import llm_handler
    path = CACHE / "answers" / (hashlib.sha256(f"{role}|{max_tokens}|{prompt}".encode()).hexdigest()[:32] + ".json")
    if path.exists():
        return tuple(json.loads(path.read_text(encoding="utf-8")))
    r = llm_handler.call_llm([{"role": "user", "content": prompt}], role=role, temperature=0,
                             max_completion_tokens=max_tokens)
    if not r["ok"]:
        sys.exit(f"stopped: {r['error']['message'][:300]}\nrerun the same command later: finished calls are cached")
    usage = r.get("usage") or {}
    out = (r["text"], usage.get("total_tokens", 0), usage.get("total_time", 0.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def small_doc(q) -> bool:
    docs = {f for f, _ in q["gold"]}
    return len(docs) == 1 and docs <= set(SMALL_DOCS)


def pick(qs, per_category=3):
    """3 per category, every unanswerable one, and every one a small document answers alone
    (those are asked twice: from retrieval and from the whole document)."""
    out, seen = [], {}
    for q in qs:
        n = seen.get(q["category"], 0)
        if q["category"] == "none" or n < per_category or small_doc(q):
            out.append(q)
            seen[q["category"]] = n + 1
    return out


def cmd_answer(args):
    paths = corpus()
    index = build_index(paths, visual=False)
    pages = [p for path in paths for p in rag.extract(path)]
    full = {name: "\n\n".join(f"[{p['file']} p.{p['page']}]\n{p['text']}" for p in pages if p["file"] == name)
            for name in SMALL_DOCS}
    rows = []
    for q in pick(questions()):
        modes = {}
        hits = index.search(q["question"], "hybrid", k=5)["hits"]
        modes["rag-hybrid"] = "\n\n".join(f"[{h['file']} p.{h['page']}]\n{h['text']}" for h in hits)
        if small_doc(q):
            modes["full-document"] = full[q["gold"][0][0]]
        for mode, context in modes.items():
            answer, tokens, sec = ask(args.role, ANSWER_PROMPT.format(question=q["question"], context=context))
            verdict, judge_tokens, _ = ask("reviewer", JUDGE_PROMPT.format(
                question=q["question"], reference=q["answer"], answer=answer), max_tokens=800)
            label = (verdict.strip().split() or ["?"])[0].strip("*:.").upper()
            if "not in the documents" in answer.lower():     # a refusal is read from the answer itself,
                label = "REFUSED"                             # not from how the judge chose to word it
            rows.append({"id": q["id"], "category": q["category"], "mode": mode, "verdict": label,
                         "answer_tokens": tokens, "server_sec": round(sec, 2), "judge_tokens": judge_tokens,
                         "answer": " ".join(answer.split())[:300]})
            print(f"{q['id']} {q['category']:<8} {mode:<14} {label:<8} {tokens:>6} tok", file=sys.stderr)
    save("answer_raw", rows)
    summary = []
    for mode in dict.fromkeys(r["mode"] for r in rows):
        for group, keep in (("answerable", lambda r: r["category"] != "none"), ("unanswerable", lambda r: r["category"] == "none")):
            g = [r for r in rows if r["mode"] == mode and keep(r)]
            if not g:
                continue
            want = "REFUSED" if group == "unanswerable" else "CORRECT"
            summary.append({"mode": mode, "questions": group, "n": len(g),
                            "correct": sum(r["verdict"] == want for r in g) / len(g),
                            "partial": sum(r["verdict"] == "PARTIAL" for r in g) / len(g),
                            "tokens_per_answer": sum(r["answer_tokens"] for r in g) / len(g)})
    # the fair comparison: the same questions both ways
    both = {r["id"] for r in rows if r["mode"] == "full-document"}
    for mode in ("rag-hybrid", "full-document"):
        g = [r for r in rows if r["mode"] == mode and r["id"] in both]
        if g:
            summary.append({"mode": mode, "questions": "small-doc subset", "n": len(g),
                            "correct": sum(r["verdict"] == "CORRECT" for r in g) / len(g),
                            "partial": sum(r["verdict"] == "PARTIAL" for r in g) / len(g),
                            "tokens_per_answer": sum(r["answer_tokens"] for r in g) / len(g)})
    save("answer", summary)
    print(table(summary))


# ---------- main ----------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ocr").set_defaults(func=cmd_ocr)
    cost = sub.add_parser("cost")
    cost.add_argument("--repeats", type=int, default=3, help="timed builds per ingestion stage")
    cost.add_argument("--query-repeats", type=int, default=15, help="passes over queries x types")
    cost.set_defaults(func=cmd_cost)
    quality = sub.add_parser("quality")
    quality.add_argument("--types", nargs="+", default=list(RULE_TYPES), choices=rag.TYPES)
    quality.add_argument("--llm-role", default="actor", help="runtime.yaml role for the -llm types")
    quality.set_defaults(func=cmd_quality)
    thai = sub.add_parser("thai")
    thai.add_argument("--no-ocr", action="store_true", help="skip the OCR part (it needs Groq tokens)")
    thai.set_defaults(func=cmd_thai)
    answer = sub.add_parser("answer")
    answer.add_argument("--role", default="actor", help="runtime.yaml role that writes the answers")
    answer.set_defaults(func=cmd_answer)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
