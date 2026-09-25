"""Trace database: every session and every action the agent took, in SQLite.

    conn = connect("sandbox/trace.db")          # creates the tables if needed; ":memory:" for tests
    sid = start_session(conn, workspace, workflow=..., model=..., task=...)
    step(conn, sid, run=1, step_id="act", tool="bash", args={...}, decision="ask_yes", ...)
    finish(conn, sid, status="done", runs=4, total_tokens=2913, answer="...")

    sessions(conn)  session(conn, sid)  steps(conn, sid)  last_session(conn)  find(conn, text)
    tool_usage(conn)

One row per step, with the field set smolagents keeps per ActionStep (tool call, model output,
observation, tokens, timing) plus one column of our own: `decision`. It records what the
permission layer did with the call - allow, ask_yes, ask_always, ask_no, deny, invalid - so the
trace answers "what did the agent try and get refused", not only "what did it run".

Nothing here ever holds a key: arguments are what the model wrote, and observations come back
from tools whose child processes never see the secrets (sandbox.scrub_env).
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import sandbox

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,   -- <started>-<workspace folder>, e.g. 20260925-101549-run_003
    started_at   TEXT,
    finished_at  TEXT,
    workflow     TEXT,
    model        TEXT,
    task         TEXT,
    status       TEXT,
    runs         INTEGER,
    total_tokens INTEGER,
    answer       TEXT,
    workspace    TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(id),
    run          INTEGER,
    step_id      TEXT,                -- the yaml step: act, review, ...
    tool         TEXT,                -- NULL for a plain llm step such as the review
    args_json    TEXT,
    decision     TEXT,                -- allow ask_yes ask_always ask_no deny invalid final_answer n/a
    ok           INTEGER,
    model_output TEXT,
    observation  TEXT,
    duration_ms  INTEGER,
    tokens       INTEGER,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS steps_by_session ON steps(session_id, id);
"""
TEXT_CAP = 4000   # per stored text field; the same order of size the model itself sees


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: str = "sandbox/trace.db") -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def start_session(conn, workspace: Path, **info) -> str:
    sid = f"{datetime.now():%Y%m%d-%H%M%S}-{Path(workspace).name}"
    conn.execute("INSERT INTO sessions (id, started_at, workflow, model, task, workspace) VALUES (?,?,?,?,?,?)",
                 (sid, _now(), info.get("workflow"), info.get("model"), info.get("task"), str(workspace)))
    conn.commit()
    return sid


def step(conn, session_id: str, *, run: int, step_id: str, tool: str | None = None, args: dict | None = None,
         decision: str = "n/a", ok: bool = True, model_output: str = "", observation: str = "",
         duration_ms: int = 0, tokens: int = 0) -> None:
    conn.execute(
        "INSERT INTO steps (session_id, run, step_id, tool, args_json, decision, ok, model_output,"
        " observation, duration_ms, tokens, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, run, step_id, tool,
         sandbox.truncate(json.dumps(args, ensure_ascii=False), TEXT_CAP) if args is not None else None,
         decision, int(ok), sandbox.truncate(model_output or "", TEXT_CAP),
         sandbox.truncate(observation or "", TEXT_CAP), duration_ms, tokens, _now()))
    conn.commit()


def finish(conn, session_id: str, *, status: str, runs: int, total_tokens: int, answer: str) -> None:
    conn.execute("UPDATE sessions SET finished_at=?, status=?, runs=?, total_tokens=?, answer=? WHERE id=?",
                 (_now(), status, runs, total_tokens, answer, session_id))
    conn.commit()


def sessions(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def steps(conn, session_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM steps WHERE session_id=? ORDER BY id", (session_id,))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["args"] = json.loads(d.pop("args_json")) if d.get("args_json") else None
        except json.JSONDecodeError:   # truncated past TEXT_CAP; keep the raw text
            d["args"] = d.pop("args_json", None)
        out.append(d)
    return out


def session(conn, session_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def last_session(conn) -> str | None:
    row = conn.execute("SELECT id FROM sessions ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def find(conn, text: str) -> str:
    """A session id from an exact id or any unique part of one (e.g. "run_003")."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM sessions WHERE id = ? OR id LIKE ? ORDER BY id",
                                         (text, f"%{text}%"))]
    if text in ids:
        return text
    if len(ids) == 1:
        return ids[0]
    raise ValueError(f"no session matches '{text}'" if not ids
                     else f"'{text}' matches {len(ids)} sessions: {', '.join(ids[:5])}")


def tool_usage(conn) -> list[dict]:
    rows = conn.execute("SELECT tool, decision, COUNT(*) AS calls FROM steps WHERE tool IS NOT NULL"
                        " GROUP BY tool, decision ORDER BY tool, calls DESC")
    return [dict(r) for r in rows]
