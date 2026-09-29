"""The tools the agent can call. Every tool gets ctx = {"workspace": Path, "sandbox": cfg}
and keyword arguments taken from the model's action. Each returns a string: the observation.

The names, the argument names and the error strings follow opencode (MIT), because those are
the shapes models have seen most; the implementations are our own. What each tool is, and what
the model is told about it, lives in config/tools.json - not here.

File paths are confined to the workspace: "../x" or "/etc/passwd" raise an error that goes back
to the model as feedback instead of touching the host. The web tools' machinery lives in web.py. `bash` is the exception by nature: a shell
command runs with the user's own authority, which is why its permission defaults to "ask".
"""
import fnmatch
import re
from pathlib import Path

import sandbox
import web

ENGINE_FILES = ("session.json", "transcript.json")   # the session's own record, kept next to its files


def _path(ctx, path: str) -> Path:
    ws = ctx["workspace"].resolve()
    p = (ws / path).resolve()
    if not p.is_relative_to(ws):
        raise ValueError(f"path '{path}' is outside the workspace")
    if p.parent == ws and p.name in ENGINE_FILES:
        raise ValueError(f"'{p.name}' is the engine's record of this session; use another name")
    return p


def _files(ctx, path: str = ""):
    """Every real file under the workspace (or under `path`), skipping our own .exec records."""
    ws = ctx["workspace"]
    root = _path(ctx, path) if path else ws
    return [p for p in sorted(root.rglob("*")) if p.is_file() and ".exec" not in p.parts
            and not (p.parent == ws and p.name in ENGINE_FILES)]


def bash(ctx, command: str, timeout: int = 0) -> str:
    """bash(command, timeout) - execute one shell command inside the workspace.
    timeout 0 means the sandbox.timeout_sec from the workflow yaml."""
    sb = ctx["sandbox"]
    secs = max(1, min(int(timeout) or sb.get("timeout_sec", 30), sandbox.MAX_TIMEOUT_SEC))
    r = sandbox.run(command, ctx["workspace"], secs, sb["max_output_chars"])
    out = f"exit {r['exit_code']}" + (" (timed out)" if r["timed_out"] else "")
    if r["stdout"]:
        out += "\nstdout:\n" + r["stdout"].rstrip("\n")
    if r["stderr"]:
        out += "\nstderr:\n" + r["stderr"].rstrip("\n")
    return out


def read(ctx, path: str, offset: int = 1, limit: int = 2000) -> str:
    """read(path, offset, limit) - read a workspace file, one page of numbered lines"""
    lines = _path(ctx, path).read_text().splitlines()
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
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {path} ({len(content)} chars)"


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


TOOLS = {f.__name__: f for f in (bash, read, write, edit, glob, grep, webfetch, websearch)}

FINAL_ANSWER_DOC = "final_answer(answer) - call this when the task is complete; answer is the reply to the user"


def snapshot(ctx) -> str:
    """Workspace listing plus (truncated) contents, so the reviewer can see what was produced.
    Capped per file and in total, so many files cannot flood the model's context."""
    ws = ctx["workspace"]
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
