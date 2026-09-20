"""Tools the agent can call. Every tool gets ctx = {"workspace": Path, "sandbox": cfg}
and keyword args from the model's JSON action. Returns a string: the observation.

File paths are confined to the workspace: "../x" or "/etc/passwd" raise an error
that goes back to the model as feedback instead of touching the host.
The first docstring line of each tool is shown to the model as its signature.
"""
import ipaddress
import socket
import inspect
from pathlib import Path
from urllib.parse import urlparse

import requests

import sandbox


def _path(ctx, path: str) -> Path:
    ws = ctx["workspace"].resolve()
    p = (ws / path).resolve()
    if not p.is_relative_to(ws):
        raise ValueError(f"path '{path}' is outside the workspace")
    return p


def write_file(ctx, path: str, content: str) -> str:
    """write_file(path, content) - create or overwrite a text file in the workspace"""
    p = _path(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {path} ({len(content)} chars)"


def read_file(ctx, path: str) -> str:
    """read_file(path) - return the contents of a workspace file"""
    return sandbox.truncate(_path(ctx, path).read_text(), ctx["sandbox"]["max_output_chars"])


def list_files(ctx) -> str:
    """list_files() - list the files in the workspace with sizes"""
    ws = ctx["workspace"]
    files = [p for p in ws.rglob("*") if p.is_file() and ".exec" not in p.parts]
    if not files:
        return "(workspace is empty)"
    return "\n".join(f"{p.relative_to(ws)}  {p.stat().st_size} bytes" for p in sorted(files))


def run_python(ctx, code: str = "", path: str = "") -> str:
    """run_python(code) or run_python(path) - run Python source (not a shell command) inside the workspace; returns exit code and output"""
    if path:
        code = _path(ctx, path).read_text()
    if not code:
        raise ValueError("give either code (Python source) or path (a .py file in the workspace)")
    sb = ctx["sandbox"]
    r = sandbox.run(code, ctx["workspace"], sb["timeout_sec"], sb["max_output_chars"])
    out = f"exit {r['exit_code']}" + (" (timed out)" if r["timed_out"] else "")
    if r["stdout"]:
        out += "\nstdout:\n" + r["stdout"].rstrip("\n")
    if r["stderr"]:
        out += "\nstderr:\n" + r["stderr"].rstrip("\n")
    return out


def http_get(ctx, url: str) -> str:
    """http_get(url) - fetch a public http(s) URL and return the start of the response body as text"""
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
        body = r.raw.read(limit + 1, decode_content=True).decode(r.encoding or "utf-8", errors="replace")
    more = " ... [truncated]" if len(body) > limit else ""
    return f"status: {r.status_code}\n{body[:limit]}{more}"


TOOLS = {f.__name__: f for f in (write_file, read_file, list_files, run_python, http_get)}


FINAL_ANSWER_DOC = "final_answer(answer) - call this when the task is complete; answer is the reply to the user"


def describe(names: list[str]) -> str:
    """One line per allowed tool, for the system prompt (text protocol)."""
    return "\n".join([TOOLS[n].__doc__ for n in names] + [FINAL_ANSWER_DOC])


def schemas(names: list[str]) -> list[dict]:
    """OpenAI-style function schemas for the same tools (native tool-calling protocol).
    Every argument is a string; arguments with a default are optional."""
    out = []
    for name in names + ["final_answer"]:
        if name == "final_answer":
            params, doc = {"answer": inspect.Parameter.empty}, FINAL_ANSWER_DOC
        else:
            sig = inspect.signature(TOOLS[name])
            params = {k: v.default for k, v in sig.parameters.items() if k != "ctx"}
            doc = TOOLS[name].__doc__
        out.append({"type": "function", "function": {
            "name": name,
            "description": doc.split(" - ", 1)[-1],
            "parameters": {"type": "object",
                           "properties": {k: {"type": "string"} for k in params},
                           "required": [k for k, d in params.items() if d is inspect.Parameter.empty]}}})
    return out


def snapshot(ctx) -> str:
    """Workspace listing plus (truncated) contents, so the reviewer can see what was produced.
    Capped per file and in total, so many files cannot flood the model's context."""
    ws = ctx["workspace"]
    per_file = ctx["sandbox"]["max_output_chars"] // 2
    total = ctx["sandbox"]["max_output_chars"] * 3
    files = [p for p in sorted(ws.rglob("*")) if p.is_file() and ".exec" not in p.parts]
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
