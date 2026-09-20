"""Tools the agent can call. Every tool gets ctx = {"workspace": Path, "sandbox": cfg}
and keyword args from the model's JSON action. Returns a string: the observation.

File paths are confined to the workspace: "../x" or "/etc/passwd" raise an error
that goes back to the model as feedback instead of touching the host.
The first docstring line of each tool is shown to the model as its signature.
"""
from pathlib import Path

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
    """http_get(url) - fetch a URL and return the response body as text"""
    r = requests.get(url, timeout=15)
    return f"status: {r.status_code}\n" + sandbox.truncate(r.text, ctx["sandbox"]["max_output_chars"])


TOOLS = {f.__name__: f for f in (write_file, read_file, list_files, run_python, http_get)}


def describe(names: list[str]) -> str:
    """One line per allowed tool, for the system prompt."""
    lines = [TOOLS[n].__doc__ for n in names]
    lines.append("final_answer(answer) - call this when the task is complete; answer is the reply to the user")
    return "\n".join(lines)


def snapshot(ctx) -> str:
    """Workspace listing plus (truncated) contents, so the reviewer can see what was produced."""
    ws = ctx["workspace"]
    per_file = ctx["sandbox"]["max_output_chars"] // 2
    out = []
    for p in sorted(ws.rglob("*")):
        if not p.is_file() or ".exec" in p.parts:
            continue
        try:
            body = sandbox.truncate(p.read_text(), per_file)
        except UnicodeDecodeError:
            body = "(binary)"
        out.append(f"### {p.relative_to(ws)} ({p.stat().st_size} bytes)\n{body}")
    return "\n\n".join(out) or "(workspace is empty)"
