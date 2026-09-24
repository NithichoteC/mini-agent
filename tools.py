"""The tools the agent can call. Every tool gets ctx = {"workspace": Path, "sandbox": cfg}
and keyword arguments taken from the model's action. Each returns a string: the observation.

The names, the argument names and the error strings follow opencode (MIT), because those are
the shapes models have seen most; the implementations are our own. What each tool is, and what
the model is told about it, lives in config/tools.json - not here.

File paths are confined to the workspace: "../x" or "/etc/passwd" raise an error that goes back
to the model as feedback instead of touching the host. `bash` is the exception by nature: a shell
command runs with the user's own authority, which is why its permission defaults to "ask".
"""
import fnmatch
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

import sandbox

TAVILY_URL = "https://api.tavily.com/search"


def _path(ctx, path: str) -> Path:
    ws = ctx["workspace"].resolve()
    p = (ws / path).resolve()
    if not p.is_relative_to(ws):
        raise ValueError(f"path '{path}' is outside the workspace")
    return p


def _files(ctx, path: str = ""):
    """Every real file under the workspace (or under `path`), skipping our own .exec records."""
    root = _path(ctx, path) if path else ctx["workspace"]
    return [p for p in sorted(root.rglob("*")) if p.is_file() and ".exec" not in p.parts]


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


class _Text(HTMLParser):
    """Visible text only: everything inside <script> or <style> is dropped."""

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def webfetch(ctx, url: str) -> str:
    """webfetch(url) - fetch a public http(s) URL and return its text"""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"only http(s) URLs are allowed, got '{url}'")
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:   # loopback, private LAN, link-local, cloud metadata
            raise ValueError(f"'{u.hostname}' resolves to a non-public address ({ip}); refused")
    limit = ctx["sandbox"]["max_output_chars"]
    with requests.get(url, timeout=15, stream=True, allow_redirects=False) as r:
        if 300 <= r.status_code < 400:
            return f"status: {r.status_code} redirect to {r.headers.get('location')} (request that URL if you want it)"
        kind = r.headers.get("content-type", "").split(";")[0].strip()
        raw = r.raw.read(4 * limit + 1, decode_content=True)
        if kind and not (kind.startswith("text/") or kind.endswith("json") or kind.endswith("xml")):
            return f"status: {r.status_code}\ncontent-type: {kind}, {len(raw)}+ bytes - not text, not shown"
        body = raw.decode(r.encoding or "utf-8", errors="replace")
    if kind == "text/html":
        body = html_to_text(body)
    more = " ... [truncated]" if len(body) > limit else ""
    return f"status: {r.status_code}\n{body[:limit]}{more}"


def websearch(ctx, query: str, max_results: int = 5) -> str:
    """websearch(query, max_results) - search the web and return titles, urls and snippets"""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return "websearch is unavailable: TAVILY_API_KEY is not set (try webfetch instead)"
    n = max(1, min(int(max_results), 10))
    r = requests.post(TAVILY_URL, timeout=20, headers={"Authorization": f"Bearer {key}"},
                      json={"query": query, "max_results": n, "search_depth": "basic"})
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        return f"no search results for '{query}'"
    out = "\n\n".join(f"{i}. {hit.get('title', '')}\n   {hit.get('url', '')}\n   "
                      + " ".join((hit.get("content") or "").split())
                      for i, hit in enumerate(results[:n], 1))
    return sandbox.truncate(out, ctx["sandbox"]["max_output_chars"])


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
