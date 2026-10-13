"""The tools the agent can call. Every tool gets ctx = {"workspace": Path, "sandbox": cfg, ...}
and keyword arguments taken from the model's action. Each returns a string: the observation.

ctx["workspace"] is the folder the file tools work in: the user's notes folder when the workflow sets
workspace.dir, else the session's own folder. ctx["session"] is always the session's folder, where its
records (.exec, transcript.json) and attachments live. Every change a tool makes to the workspace is
reported through changed(): the workspace index re-reads that file and ctx["on_change"] tells the
caller (the web UI refreshes its file tree and editor from it).

The names, the argument names and the error strings follow opencode (MIT), because those are
the shapes models have seen most; the implementations are our own. What each tool is, and what
the model is told about it, lives in config/tools.json - not here.

File paths are confined to the workspace: "../x" or "/etc/passwd" raise an error that goes back
to the model as feedback instead of touching the host. The web tools' machinery lives in web.py. `bash` is the exception by nature: a shell
command runs with the user's own authority, which is why its permission defaults to "ask".

Files the user attaches go through attach(): a short one is handed to the model whole, a long one
is indexed into the workspace's .rag/ folder (rag.py) and read back with rag_search - ChatGPT's
pattern for uploads, at our scale. The index lives and dies with the session's workspace.
"""
import fnmatch
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from agentic_ai.integrations.web import fetch as web
from agentic_ai.retrieval import rag
from agentic_ai.runtime import sandbox

ENGINE_FILES = ("session.json", "transcript.json")   # the session's own record, kept next to its files
HIDDEN = (".exec", ".rag", ".aiblue", ".git")        # engine and tool folders: not the agent's files
ENGINE_DIR = ".aiblue"                               # the workspace's own folder: index, trash


def _session(ctx) -> Path:
    return Path(ctx.get("session") or ctx["workspace"])


def _path(ctx, path: str) -> Path:
    ws = ctx["workspace"].resolve()
    p = (ws / path).resolve()
    if not p.is_relative_to(ws):
        raise ValueError(f"path '{path}' is outside the workspace")
    if p != ws and p.relative_to(ws).parts[0] in HIDDEN:
        raise ValueError(f"'{path}' is inside {p.relative_to(ws).parts[0]}/, which belongs to the engine")
    if p.parent == ws and p.name in ENGINE_FILES and _session(ctx).resolve() == ws:
        raise ValueError(f"'{p.name}' is the engine's record of this session; use another name")
    return p


def _rel(ctx, p: Path) -> str:
    return p.resolve().relative_to(ctx["workspace"].resolve()).as_posix()


def _files(ctx, path: str = ""):
    """Every real file under the workspace (or under `path`), skipping engine folders and records."""
    ws = ctx["workspace"]
    root = _path(ctx, path) if path else ws
    same = _session(ctx).resolve() == ws.resolve()
    return [p for p in sorted(root.rglob("*")) if p.is_file()
            and not any(h in p.relative_to(ws).parts for h in HIDDEN)
            and not (same and p.parent == ws and p.name in ENGINE_FILES)]


def changed(ctx, kind: str, path: str, dest: str | None = None) -> None:
    """A tool changed the workspace: keep the index in step, then tell the caller.
    kind is write | delete | move | mkdir; paths are relative to the workspace."""
    index = _workspace_indexes.get(str(ctx["workspace"].resolve()))
    if index is not None:
        try:
            if kind == "write":
                index.index_file(path)
            elif kind == "delete":
                index.remove(path)
            elif kind == "move":
                index.move(path, dest)
        except Exception as e:                          # the edit happened; a stale index heals on the next scan
            ctx.setdefault("warnings", []).append(f"index not updated for {path}: {e}")
    ctx.setdefault("changes", []).append({"kind": kind, "path": path, **({"dest": dest} if dest else {})})
    if ctx.get("on_change"):
        ctx["on_change"]({"kind": kind, "path": path, **({"dest": dest} if dest else {})})


def bash(ctx, command: str, timeout: int = 0) -> str:
    """bash(command, timeout) - execute one shell command inside the workspace.
    timeout 0 means the sandbox.timeout_sec from the workflow yaml."""
    sb = ctx["sandbox"]
    secs = max(1, min(int(timeout) or sb.get("timeout_sec", 30), sandbox.MAX_TIMEOUT_SEC))
    r = sandbox.run(command, ctx["workspace"], secs, sb["max_output_chars"], records=_session(ctx))
    out = f"exit {r['exit_code']}" + (" (timed out)" if r["timed_out"] else "")
    if r["stdout"]:
        out += "\nstdout:\n" + r["stdout"].rstrip("\n")
    if r["stderr"]:
        out += "\nstderr:\n" + r["stderr"].rstrip("\n")
    return out


def read(ctx, path: str, offset: int = 1, limit: int = 2000) -> str:
    """read(path, offset, limit) - read a workspace file, one page of numbered lines"""
    p = _path(ctx, path)
    try:
        lines = p.read_text().splitlines()
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not a text file ({p.stat().st_size} bytes); an attached document "
                         "is read through rag_search, or was given in full in the message") from None
    start = max(1, int(offset))
    page = lines[start - 1:start - 1 + max(1, int(limit))]
    if not page:
        return f"(no lines at offset {start}; {path} has {len(lines)} lines)"
    body = "\n".join(f"{i:>5}| {line}" for i, line in enumerate(page, start))
    rest = len(lines) - (start - 1 + len(page))
    if rest > 0:
        body += f"\n... {rest} more line(s); read again with offset={start + len(page)}"
    return sandbox.truncate(body, ctx["sandbox"]["max_output_chars"])


def write(ctx, path: str, content: str) -> str:
    """write(path, content) - create or overwrite a text file in the workspace"""
    p = _path(ctx, path)
    if p.is_dir():
        raise ValueError(f"{path} is a folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    changed(ctx, "write", _rel(ctx, p))
    return f"wrote {path} ({len(content)} chars)"


def append(ctx, path: str, content: str) -> str:
    """append(path, content) - add text to the end of a file (created if missing)"""
    p = _path(ctx, path)
    if p.is_dir():
        raise ValueError(f"{path} is a folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text() if p.exists() else ""
    sep = "" if not old or old.endswith("\n") else "\n"
    p.write_text(old + sep + content)
    changed(ctx, "write", _rel(ctx, p))
    return f"appended {len(content)} chars to {path}"


def mkdir(ctx, path: str) -> str:
    """mkdir(path) - create a folder (and any missing parents)"""
    p = _path(ctx, path)
    if p.exists():
        return f"{path} already exists" + ("" if p.is_dir() else " and is a file")
    p.mkdir(parents=True)
    changed(ctx, "mkdir", _rel(ctx, p))
    return f"created folder {path}"


def move(ctx, source: str, destination: str) -> str:
    """move(source, destination) - rename or move a file or folder; never overwrites"""
    src, dst = _path(ctx, source), _path(ctx, destination)
    if not src.exists():
        raise ValueError(f"{source} does not exist")
    if src == ctx["workspace"].resolve():
        raise ValueError("the workspace itself cannot be moved")
    if dst.is_dir():
        dst = dst / src.name                      # "move a.md notes" puts it inside the notes folder
    if dst.exists():
        raise ValueError(f"{_rel(ctx, dst)} already exists; pick another name or delete it first")
    if src.is_dir() and dst.is_relative_to(src):
        raise ValueError("a folder cannot be moved into itself")
    dst.parent.mkdir(parents=True, exist_ok=True)
    old = _rel(ctx, src)
    shutil.move(str(src), str(dst))
    changed(ctx, "move", old, _rel(ctx, dst))
    return f"moved {old} -> {_rel(ctx, dst)}"


def delete(ctx, path: str) -> str:
    """delete(path) - move a file or folder to the workspace trash (.aiblue/trash), where it can be restored"""
    p = _path(ctx, path)
    if not p.exists():
        raise ValueError(f"{path} does not exist")
    if p == ctx["workspace"].resolve():
        raise ValueError("the workspace itself cannot be deleted")
    rel = _rel(ctx, p)
    trash = ctx["workspace"] / ENGINE_DIR / "trash" / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / rel
    trash.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(trash))
    changed(ctx, "delete", rel)
    return f"deleted {rel} (kept in {ENGINE_DIR}/trash/{trash.relative_to(ctx['workspace'] / ENGINE_DIR / 'trash').as_posix()})"


def list_dir(ctx, path: str = "", depth: int = 2) -> str:
    """list(path, depth) - the folders and files under a folder, as a tree"""
    root = _path(ctx, path) if path else ctx["workspace"].resolve()
    if not root.is_dir():
        raise ValueError(f"{path} is not a folder")
    depth = max(1, min(int(depth), 6))
    lines, shown, limit = [f"{path or '.'}/"], 0, 300

    def walk(folder: Path, level: int):
        nonlocal shown
        entries = sorted((e for e in folder.iterdir() if e.name not in HIDDEN and not e.name.startswith(".")),
                         key=lambda e: (e.is_file(), e.name.lower()))
        for e in entries:
            if shown >= limit:
                return
            shown += 1
            if e.is_dir():
                inside = sum(1 for _ in e.iterdir())
                lines.append(f"{'  ' * level}{e.name}/" + ("" if level < depth else f"  ({inside} item(s))"))
                if level < depth:
                    walk(e, level + 1)
            else:
                lines.append(f"{'  ' * level}{e.name}  {e.stat().st_size} bytes")

    walk(root, 1)
    if shown >= limit:
        lines.append(f"... stopped at {limit} entries; list a subfolder")
    return "\n".join(lines) if len(lines) > 1 else f"{path or 'the workspace'} is empty"


def edit(ctx, path: str, oldString: str, newString: str, replaceAll: bool = False) -> str:
    """edit(path, oldString, newString, replaceAll) - replace exact text in a workspace file"""
    if oldString == newString:
        raise ValueError("No changes to apply: oldString and newString are identical.")
    if oldString == "":
        raise ValueError("oldString must not be empty. Use write to create or overwrite a file.")
    p = _path(ctx, path)
    text = p.read_text()
    hits = text.count(oldString)
    if hits == 0:
        raise ValueError(f"oldString did not match any text in {path}.")
    if hits > 1 and not replaceAll:
        raise ValueError(f"oldString matched {hits} times in {path}; add more context or pass replaceAll.")
    p.write_text(text.replace(oldString, newString) if replaceAll else text.replace(oldString, newString, 1))
    changed(ctx, "write", _rel(ctx, p))
    return f"edited {path} ({hits if replaceAll else 1} replacement(s))"


def glob(ctx, pattern: str = "*", path: str = "", limit: int = 100) -> str:
    """glob(pattern, path, limit) - find workspace files whose path matches a pattern"""
    ws = ctx["workspace"]
    hits = [p for p in _files(ctx, path) if fnmatch.fnmatch(str(p.relative_to(ws)), pattern)]
    if not hits:
        return f"no files match '{pattern}'"
    out = "\n".join(f"{p.relative_to(ws)}  {p.stat().st_size} bytes" for p in hits[:limit])
    return out + (f"\n... {len(hits) - limit} more" if len(hits) > limit else "")


def grep(ctx, pattern: str, path: str = "", include: str = "", limit: int = 50) -> str:
    """grep(pattern, path, include, limit) - search workspace file contents with a regex"""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"invalid regex '{pattern}': {e}")
    ws, out = ctx["workspace"], []
    for p in _files(ctx, path):
        rel = str(p.relative_to(ws))
        if include and not fnmatch.fnmatch(rel, include):
            continue
        try:
            lines = p.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                out.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(out) >= limit:
                    return "\n".join(out) + f"\n... stopped at the limit of {limit} matches"
    return "\n".join(out) if out else f"no matches for '{pattern}'"


def webfetch(ctx, url: str, format: str = "markdown") -> str:
    """webfetch(url, format) - fetch a public URL as markdown (default), text or html"""
    cfg = ctx.get("web") or {}
    page = web.fetch(url, format, cfg.get("fetch_fallback", "firecrawl"))
    limit = ctx["sandbox"]["max_output_chars"]
    head = f"status: {page['status']} \u00b7 via {page['source']}" + (f" ({page['note']})" if page.get("note") else "")
    head += f"\nurl: {page['url']}" + (f"\ntitle: {page['title']}" if page.get("title") else "")
    text = page["text"]
    more = f"\n... [truncated: {len(text) - limit} more chars]" if len(text) > limit else ""
    return f"{head}\n\n{text[:limit]}{more}"


def websearch(ctx, query: str, max_results: int = 5) -> str:
    """websearch(query, max_results) - search the web; returns titles, urls and snippets"""
    cfg = ctx.get("web") or {}
    out = web.search(query, max(1, min(int(max_results), 10)), cfg.get("search", ["firecrawl", "exa"]))
    if not out["provider"]:
        return "websearch failed - " + "; ".join(out["errors"]) + " (try webfetch on a URL you know)"
    head = f"results via {out['provider']} ({'keyed' if out['keyed'] else 'keyless'})"
    if "results" in out:
        body = "\n\n".join(f"{i}. {h['title']}\n   {h['url']}\n   {h['snippet']}"
                            for i, h in enumerate(out["results"], 1))
    else:
        body = out["text"]
    limit = ctx["sandbox"]["max_output_chars"]
    return f"{head}:\n\n{body}"[:limit]


def attach(ctx, source) -> str:
    """Copy a user's file into the session's folder and return the note that goes into their message:
    the full text when it is short (rag.full_text_tokens), otherwise a pointer to rag_search.
    When the session folder is the workspace the file is one of the agent's files; with a shared notes
    folder it stays with the session, so a chat upload never lands in the user's notes unasked."""
    cfg = ctx.get("rag") or {}
    src = Path(source).expanduser()
    dest = _path(ctx, src.name) if _session(ctx).resolve() == ctx["workspace"].resolve() else _session(ctx) / src.name
    shutil.copyfile(src, dest)
    try:
        ocr = rag.groq_ocr(_session(ctx) / ".rag" / "ocr") if cfg.get("ocr", False) else None
        pages = rag.extract(dest, ocr=ocr)
    except ImportError as e:
        return f"Attached file {dest.name}: cannot read it here ({e.name} is not installed; see requirements-rag.txt)."
    except Exception as e:  # a broken file is the user's news, not a crash
        return f"Attached file {dest.name}: could not be read ({type(e).__name__}: {e})."
    text = "\n\n".join(f"[{p['file']} p.{p['page']}{OCR_LABEL if p['method'] == 'ocr' else ''}]\n{p['text']}"
                       for p in pages)
    tokens, limit = len(text) // 4, int(cfg.get("full_text_tokens", 4000))
    unread = [str(p["page"]) for p in pages if p["method"].startswith("native, ")]
    why = "OCR failed" if any("OCR failed" in p["method"] for p in pages) else "OCR is off (rag.ocr)"
    head = (f"Attached file {dest.name} ({len(pages)} page(s), about {tokens:,} tokens"
            + (f"; page(s) {', '.join(unread)} have no text layer (scanned or images) and were not read: {why}"
               if unread else "") + ")")
    if tokens <= limit:
        return f"{head}. Full text:\n{text}"
    try:
        chunks = _rag_add(ctx, pages)
    except ImportError as e:
        return (f"{head}. Only the first {limit:,} tokens fit here and search is not installed ({e.name}):\n"
                f"{text[:limit * 4]}\n[... the rest of the file is cut]")
    return (f"{head}. Too long to show whole: it is indexed ({chunks} passages); use rag_search to find what "
            f"you need. A question about the whole file (what is in it, a summary) is answered from this "
            f"outline plus a few searches - search cannot return the whole file.\nOutline:\n{outline(dest, pages)}")


def outline(path, pages, entries: int = 15) -> str:
    """Where the file goes, in a couple of hundred tokens: its headings when it is a PDF that has
    them, else the opening words of passages spread evenly through it."""
    try:
        heads = rag.headings(path)
    except Exception:                               # an outline is a courtesy; never fail an attach on it
        heads = []
    if len(heads) < 3:
        heads = [(c["page"], c["text"] + " ...") for c in rag.chunk(pages, words=14, overlap=0)]
    step = max(1, -(-len(heads) // entries))
    return "\n".join(f"- p.{page}: {text}" for page, text in heads[::step][:entries])


# OCR on the free tier reads prose well but can put wrong numbers on a page with no error (Thai: about
# half the figures exact), so every passage that came from OCR says so wherever the model sees it.
OCR_LABEL = ", read by OCR - check numbers"

_indexes = {}             # session folder -> rag.Index of its attachments, so a chat does not reload it every turn
_workspace_indexes = {}   # workspace folder -> WorkspaceIndex of the notes (one per process)
_scanned = {}             # workspace folder -> time of the last scan
SCAN_EVERY = 5            # seconds; a search re-checks file dates at most this often (edits made outside the tools)


def _embedder():
    try:
        return rag.E5()
    except ImportError:              # no sentence-transformers: keyword search still works
        return rag.HashEmbedder()


def _rag_add(ctx, pages) -> int:
    """Add pages to the session's index (all attachments share one index). Returns the passage count."""
    folder = _session(ctx) / ".rag"
    old = rag.Index.load(folder, _embedder()).chunks if (folder / "meta.json").exists() else []
    chunks = old + [{**c, "id": c["id"] + len(old)} for c in rag.chunk(pages)]
    index = rag.Index.build(chunks, _embedder())
    index.save(folder)
    _indexes[str(_session(ctx))] = index
    return len(chunks)


def workspace_index(ctx, fresh: bool = True):
    """The notes index of a shared workspace (None for a per-session folder), created on first use.
    `fresh` re-checks file dates (at most every SCAN_EVERY seconds) so edits made outside the tools count."""
    if not ctx.get("notes"):
        return None
    key = str(ctx["workspace"].resolve())
    if key not in _workspace_indexes:
        from agentic_ai.retrieval.workspace_index import WorkspaceIndex
        _workspace_indexes[key] = WorkspaceIndex(ctx["workspace"], _embedder())
        _scanned[key] = 0
    index = _workspace_indexes[key]
    if fresh and time.time() - _scanned[key] > SCAN_EVERY:
        index.scan()
        _scanned[key] = time.time()
    return index


def rag_search(ctx, query: str, k: int = 0, folder: str = "") -> str:
    """rag_search(query, k, folder) - the passages of the workspace notes (one folder, or all) and of the
    documents attached to this session that best match the query"""
    cfg = ctx.get("rag") or {}
    k = max(1, min(int(k) or int(cfg.get("k", 4)), 10))
    found = []                                     # (label, text) per hit, notes first
    notes = workspace_index(ctx)
    if notes is not None:
        scope = (folder or ctx.get("scope") or "").strip("/")
        if scope and not _path(ctx, scope).is_dir():
            raise ValueError(f"folder '{scope}' does not exist in the workspace")
        kind = cfg.get("type", "hybrid") if cfg.get("type", "hybrid") in ("dense", "bm25", "hybrid") else "hybrid"
        for h in notes.search(query, k=k, folder=scope, type=kind if notes.embedder.name != "hash" else "bm25"):
            where = f"{h['path']}" + (f" > {h['heading']}" if h["heading"] else "") + f" (line {h['line']})"
            found.append((where, h["text"]))
    attached, key = _session(ctx) / ".rag", str(_session(ctx))
    if (attached / "meta.json").exists():
        if key not in _indexes:
            _indexes[key] = rag.Index.load(attached, _embedder())
        index = _indexes[key]
        kind = cfg.get("type", "hybrid") if index.embedder.name != "hash" else "bm25"
        for h in index.search(query, kind, k=k, llm=rag.model_llm() if kind.endswith("-llm") else None)["hits"]:
            found.append((f"{h['file']} p.{h['page']}{OCR_LABEL if h.get('ocr') else ''}", h["text"]))
    if notes is None and not (attached / "meta.json").exists():
        raise ValueError("no attached document is indexed in this session (short files are already in the "
                         "conversation as full text; files the agent made itself can be read with read or grep)")
    if not found:
        return f"no passages match '{query}'" + (f" in {scope}/" if notes is not None and scope else "")
    # the first line names every source, so a reviewer who sees only the start can still check citations
    head = "found: " + ", ".join(f"[{i}] {label}" for i, (label, _) in enumerate(found, 1))
    per = max(200, (ctx["sandbox"]["max_output_chars"] - len(head)) // len(found) - 40)
    return head + "\n\n" + "\n\n".join(f"[{i}] {label}\n{text[:per]}" for i, (label, text) in enumerate(found, 1))


TOOLS = {f.__name__: f for f in (bash, read, write, edit, append, glob, grep, mkdir, move, delete,
                                  webfetch, websearch, rag_search)} | {"list": list_dir}

FINAL_ANSWER_DOC = "final_answer(answer) - call this when the task is complete; answer is the reply to the user"


def snapshot(ctx) -> str:
    """Workspace listing plus (truncated) contents, so the reviewer can see what was produced.
    Capped per file and in total, so many files cannot flood the model's context. In a shared notes
    folder only the files this turn changed are shown: the rest of the notes are not the turn's work."""
    ws = ctx["workspace"]
    if ctx.get("notes"):
        touched = [c.get("dest") or c["path"] for c in ctx.get("changes", [])]
        files = [ws / t for t in dict.fromkeys(touched) if (ws / t).is_file()]
        gone = [c["path"] for c in ctx.get("changes", []) if c["kind"] in ("delete", "move")]
        out = [f"### {f.relative_to(ws).as_posix()} ({f.stat().st_size} bytes)\n"
               + sandbox.truncate(f.read_text(errors="replace"), ctx["sandbox"]["max_output_chars"] // 2) for f in files]
        if gone:
            out.append("removed or moved away: " + ", ".join(gone))
        return "\n\n".join(out) or "(no files changed in this turn)"
    per_file = ctx["sandbox"]["max_output_chars"] // 2
    total = ctx["sandbox"]["max_output_chars"] * 3
    files = _files(ctx)
    out, used = [], 0
    for i, p in enumerate(files):
        try:
            body = sandbox.truncate(p.read_text(), per_file)
        except UnicodeDecodeError:
            body = "(binary)"
        entry = f"### {p.relative_to(ws)} ({p.stat().st_size} bytes)\n{body}"
        if used + len(entry) > total:
            out.append(f"... {len(files) - i} more file(s) not shown: " + ", ".join(str(q.relative_to(ws)) for q in files[i:]))
            break
        out.append(entry)
        used += len(entry)
    return "\n\n".join(out) or "(workspace is empty)"
