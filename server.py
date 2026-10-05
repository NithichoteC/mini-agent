"""The web UI's back end:  python main.py serve [--port 8000]   (deps: requirements-ui.txt)

It drives the same engine as the CLI - loop.run_workflow, tracedb, tools.attach, registry - and has
no agent loop of its own. One message = one run_workflow call in a worker thread; what it emits goes
to the browser as Server-Sent Events, one JSON object per `data:` line:

    turn           {"turn": id}                       first, so the page can tell turns apart
    session.start  a new session: its id, task, model
    step           one trace row, plus `parts` - the same parts tracedb.transcript() builds
    approval       a tool whose permission is "ask" wants a human: {"id", "tool", "args", "title", "always"}
    session.end    main.finish's summary: status, answer, total_tokens, trace_id ...
    error          the turn could not run (a bad config, an unknown session); `message` says why

An approval is answered with POST /api/approvals/{id} {"decision": "allow" | "always" | "deny", "reason"};
until then the worker blocks inside `confirm`, exactly like the CLI's y/a/n prompt. No answer within
APPROVAL_WAIT seconds, or a closed browser tab, counts as "deny".

    GET  /api/sessions                     past sessions, newest first (title = first message)
    GET  /api/sessions/{id}                the conversation (tracedb.transcript), ready to render
    GET  /api/sessions/{id}/trace          every step - the `main.py trace <id>` table
    GET  /api/sessions/{id}/transcript     transcript.json as a download
    POST /api/uploads                      multipart file -> {"id", "name", "size"}; sent later as `files`
    POST /api/chat                         {"session"?, "message", "files"?: [ids], "hint"?: bool} -> events
    POST /api/approvals/{id}               answer an approval
    POST /api/turns/{id}/stop              stop the turn before its next step (pending approvals are denied)
    GET  /api/settings   PUT /api/settings  the editable part of workflow.yaml + runtime.yaml (comments kept)
    GET  /api/meters?session=id            the last request's size vs what one request may hold (the smaller of
                                           the model's context window and the plan's request_tokens);
                                           tokens per model in the last 24 h vs daily_tokens

Settings are written back into the yaml files, so the CLI and the UI share one config; each message
reads the config afresh. Everything is validated before a byte is written.
"""
import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

import main
import rag
import registry
import tracedb
from llm_handler import ROOT, load_runtime
from loop import model_name, run_workflow

UI_DIST = ROOT / "ui" / "dist"
APPROVAL_WAIT = 600            # seconds a tool waits for a human before it counts as denied
MAX_UPLOAD = 50 * 1024 * 1024
ACTIONS = ("json_text", "tool_calls")
REVIEW = ("auto", "always", "never")
LIMITS = {"k": (1, 50), "full_text_tokens": (100, 200_000), "max_runs": (1, 100), "max_repeats": (1, 20)}


def _yaml() -> YAML:
    y = YAML()                                       # round-trip mode: comments and order survive
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def tool_registry(cfg: dict) -> dict:
    return registry.load(cfg.get("registry", registry.DEFAULT_PATH))


def split_note(text: str) -> tuple[str, list[dict]]:
    """A user message as stored ("<what they typed>\\n\\nAttached file x ...") -> (text, attachments)."""
    head, *notes = (text or "").split("\n\nAttached file ")
    files = []
    for note in notes:
        first = ("Attached file " + note).splitlines()[0].removesuffix(" Full text:")
        files.append({"name": note.split(" ", 1)[0].rstrip(":"), "note": first})
    return head, files


def decorate(parts: list[dict], reg: dict) -> list[dict]:
    """Give each tool part its human title from tools.json (the CLI's console line)."""
    for p in parts:
        if p["type"] == "tool":
            args = p["state"]["input"]
            p["title"] = registry.render(reg, p["tool"], args)[1] if isinstance(args, dict) else p["tool"]
    return parts


class Turn:
    """One message being worked on: its event queue and the approvals it is waiting for."""

    def __init__(self, session: str | None):
        self.id, self.session, self.events, self.gates = uuid.uuid4().hex[:12], session, queue.Queue(), {}
        self.stop = threading.Event()

    def put(self, event: dict) -> None:
        self.events.put(event)

    def release(self, reason: str) -> None:
        for gate in list(self.gates.values()):
            gate["answer"] = ("deny", reason)
            gate["event"].set()


def create_app(config: str = main.DEFAULT_CONFIG) -> FastAPI:
    app = FastAPI(title="mini-agent", docs_url="/api/docs", openapi_url="/api/openapi.json")
    uploads = Path(tempfile.mkdtemp(prefix="mini-agent-uploads-"))
    turns: dict[str, Turn] = {}                      # live turns, by turn id
    approvals: dict[str, dict] = {}                  # pending approval id -> gate
    lock = threading.Lock()
    app.state.approvals, app.state.turns = approvals, turns      # visible to tests

    def load() -> dict:
        return main.load_config(config)

    def runtime_path(cfg: dict) -> Path:
        p = Path(cfg.get("runtime") or "config/runtime.yaml")
        return p if p.is_absolute() else ROOT / p

    def connect(cfg: dict):
        path = main.trace_path(cfg)
        if not cfg.get("trace", {}).get("enabled", True):
            raise HTTPException(409, "the trace is off (trace.enabled: false); the UI reads sessions from it")
        return tracedb.connect(path)

    def busy() -> set:
        with lock:
            return {t.session for t in turns.values() if t.session}

    def find(conn, sid: str) -> str:
        try:
            return tracedb.find(conn, sid)
        except ValueError as e:
            raise HTTPException(404, str(e))

    # ---- sessions ---------------------------------------------------------------------------------
    @app.get("/api/sessions")
    def sessions(limit: int = 50):
        cfg, running = load(), busy()
        with closing(connect(cfg)) as conn:
            rows = tracedb.sessions(conn, limit=limit)
        return [{"id": r["id"], "title": split_note(r["task"])[0][:120], "status": r["status"],
                 "started": r["started_at"], "updated": r["finished_at"] or r["started_at"],
                 "tokens": r["total_tokens"] or 0, "running": r["id"] in running} for r in rows]

    @app.get("/api/sessions/{sid}")
    def conversation(sid: str):
        cfg = load()
        reg = tool_registry(cfg)
        with closing(connect(cfg)) as conn:
            doc = tracedb.transcript(conn, find(conn, sid))
        for m in doc["messages"]:
            if m["info"]["role"] == "user":
                m["text"], m["attachments"] = split_note(m["parts"][0]["text"])
            else:
                decorate(m["parts"], reg)
        doc["info"]["title"] = split_note(doc["info"]["title"])[0]
        doc["info"]["running"] = doc["info"]["id"] in busy()
        return doc

    @app.get("/api/sessions/{sid}/trace")
    def trace(sid: str):
        cfg = load()
        reg = tool_registry(cfg)
        with closing(connect(cfg)) as conn:
            sid = find(conn, sid)
            info, rows = tracedb.session(conn, sid), tracedb.steps(conn, sid)
        for r in rows:
            if r["tool"] and r["tool"] != "final_answer" and isinstance(r["args"], dict) and r["tool"] in reg["tools"]:
                r["title"] = registry.render(reg, r["tool"], r["args"])[1]
            else:
                r["title"] = r["tool"]
        return {"session": info, "steps": rows}

    @app.get("/api/sessions/{sid}/transcript")
    def transcript_file(sid: str):
        cfg = load()
        with closing(connect(cfg)) as conn:
            sid = find(conn, sid)
            info, doc = tracedb.session(conn, sid), tracedb.transcript(conn, sid)
        path = Path(info["workspace"]) / "transcript.json"
        name = f"{sid}-transcript.json"
        if path.exists():
            return FileResponse(path, media_type="application/json", filename=name)
        return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---- uploads ----------------------------------------------------------------------------------
    @app.post("/api/uploads")
    def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "file").name or "file"
        fid = uuid.uuid4().hex[:12]
        dest = uploads / fid / name
        dest.parent.mkdir(parents=True)
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        size = dest.stat().st_size
        if size > MAX_UPLOAD:
            shutil.rmtree(dest.parent)
            raise HTTPException(413, f"{name} is {size // 2**20} MB; the limit is {MAX_UPLOAD // 2**20} MB")
        return {"id": fid, "name": name, "size": size}

    def upload_path(fid: str) -> str:
        found = list((uploads / Path(fid).name).glob("*")) if fid else []
        if not found:
            raise HTTPException(404, f"no uploaded file '{fid}' (uploads last until the server stops)")
        return str(found[0])

    # ---- one turn ---------------------------------------------------------------------------------
    def work(turn: Turn, cfg: dict, text: str, previous: dict | None, hint: bool, files: list[str]):
        conn = tracedb.connect(main.trace_path(cfg)) if cfg.get("trace", {}).get("enabled", True) else None
        reg = tool_registry(cfg)
        log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        def log(line):
            with log_file.open("a") as f:
                f.write(line + "\n")

        def emit(event):
            if event["type"] == "session.start":
                turn.session = event["session"]
            if event["type"] == "step":
                event = {**event, "parts": decorate(tracedb.parts(event), reg)}
            turn.put(event)

        def confirm(tool, args):
            aid = uuid.uuid4().hex[:12]
            gate = {"event": threading.Event(), "answer": ("deny", f"no answer within {APPROVAL_WAIT} s")}
            turn.gates[aid] = approvals[aid] = gate
            scope = registry.always_scope(reg, tool, args)
            turn.put({"type": "approval", "id": aid, "tool": tool, "args": args,
                      "title": registry.render(reg, tool, args)[1],
                      "always": "everything" if scope == ["*"] else ", ".join(p for p in scope if not p.endswith(" *"))})
            gate["event"].wait(APPROVAL_WAIT)
            approvals.pop(aid, None)
            turn.gates.pop(aid, None)
            return gate["answer"]

        env = {"cfg": cfg, "trace_db": conn, "log": log, "emit": emit, "model": model_name(cfg), "as_json": True}
        kw = {"log": log, "confirm": confirm, "trace_db": conn, "emit": emit, "cancel": turn.stop.is_set}
        started = time.time()
        try:
            log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} (web) "
                f"{'hint' if hint else 'turn' if previous else 'task'}={text!r}")
            if hint:
                result = run_workflow(cfg, previous["state"]["task"], previous=previous, hint=text, **kw)
            else:
                result = run_workflow(cfg, text, previous=previous, attachments=files, **kw)
            main.finish(env, result, text, started)
        except Exception as e:                       # the page shows it; the server keeps serving
            turn.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            if conn is not None:
                conn.close()
            with lock:
                turns.pop(turn.id, None)
            turn.put(None)

    def stream(turn: Turn):
        try:
            yield f"data: {json.dumps({'type': 'turn', 'turn': turn.id})}\n\n"
            while True:
                try:
                    event = turn.events.get(timeout=15)
                except queue.Empty:
                    yield ": still working\n\n"          # keeps proxies from closing a quiet stream
                    continue
                if event is None:
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            turn.release("the browser disconnected")    # never leave the worker waiting on a closed tab

    @app.post("/api/chat")
    def chat(body: dict = Body(...)):
        text = str(body.get("message") or "").strip()
        sid, hint = body.get("session"), bool(body.get("hint"))
        files = [upload_path(f) for f in body.get("files") or []]
        if not text and not files:
            raise HTTPException(422, "type a message or attach a file")
        if hint and not sid:
            raise HTTPException(422, "a hint continues a session; say which one")
        cfg = load()
        previous = None
        if sid:
            conn = connect(cfg)
            try:
                sid = find(conn, sid)
                previous = main.resume({"trace_db": conn}, sid)
            except SystemExit as e:              # main.resume speaks to a terminal; same words here
                raise HTTPException(409, str(e))
            finally:
                conn.close()
        turn = Turn(sid)
        with lock:
            if sid and any(t.session == sid for t in turns.values()):
                raise HTTPException(409, f"session {sid} is still working on the last message")
            turns[turn.id] = turn
        threading.Thread(target=work, args=(turn, cfg, text, previous, hint, files), daemon=True).start()
        return StreamingResponse(stream(turn), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/approvals/{aid}")
    def approve(aid: str, body: dict = Body(...)):
        gate = approvals.get(aid)
        if gate is None:
            raise HTTPException(404, "this approval is no longer waiting (answered, timed out, or the turn ended)")
        decision = body.get("decision")
        if decision not in ("allow", "always", "deny"):
            raise HTTPException(422, "decision must be allow, always or deny")
        gate["answer"] = (decision, str(body.get("reason") or "").strip())
        gate["event"].set()
        return {"ok": True}

    @app.post("/api/turns/{tid}/stop")
    def stop(tid: str):
        with lock:
            turn = turns.get(tid)
        if turn is None:
            raise HTTPException(404, "this turn has already finished")
        turn.stop.set()
        turn.release("stopped by the user")
        return {"ok": True}

    # ---- settings ---------------------------------------------------------------------------------
    def settings_now(cfg: dict) -> dict:
        rt = load_runtime(str(runtime_path(cfg)))
        roles = rt.get("roles") or {}
        r, lp = cfg.get("rag") or {}, cfg.get("loop") or {}
        return {"actor": (roles.get("actor") or {}).get("model_ref"),
                "reviewer": (roles.get("reviewer") or {}).get("model_ref"),
                "actions": cfg["llm"].get("actions", "json_text"), "tools": list(cfg.get("tools") or []),
                "review": cfg.get("review", "always"),
                "permissions": [dict(p) for p in cfg.get("permissions") or []],
                "rag": {"type": r.get("type", "hybrid"), "k": r.get("k", 4),
                        "full_text_tokens": r.get("full_text_tokens", 4000), "ocr": bool(r.get("ocr", False))},
                "loop": {"max_runs": lp.get("max_runs", 8), "max_repeats": lp.get("max_repeats", 3)}}

    @app.get("/api/settings")
    def get_settings():
        cfg = load()
        rt = load_runtime(str(runtime_path(cfg)))
        reg = tool_registry(cfg)
        return {"values": settings_now(cfg), "files": [config, str(cfg.get("runtime"))], "options": {
            "models": [{"key": k, "model": m.get("model"), "vendor": m.get("vendor")}
                       for k, m in (rt.get("models") or {}).items()],
            "actions": list(ACTIONS), "rag_types": list(rag.TYPES), "permission_actions": list(registry.ACTIONS),
            "review": list(REVIEW),
            "tools": [{"name": n, "description": s["description"], "permission": s.get("permission", "ask")}
                      for n, s in reg["tools"].items()]}}

    def check(cfg: dict, new: dict) -> None:
        """Every value must be one the engine accepts; the first problem is the error message."""
        models = (load_runtime(str(runtime_path(cfg))).get("models") or {})
        for role in ("actor", "reviewer"):
            if new[role] not in models:
                raise ValueError(f"{role}: '{new[role]}' is not a model in runtime.yaml (have: {', '.join(models)})")
        if new["actions"] not in ACTIONS:
            raise ValueError(f"actions must be one of {', '.join(ACTIONS)}")
        if new["review"] not in REVIEW:
            raise ValueError(f"review must be one of {', '.join(REVIEW)}")
        if not isinstance(new["tools"], list) or not all(isinstance(t, str) for t in new["tools"]):
            raise ValueError("tools must be a list of tool names")
        registry.check(tool_registry(cfg), new["tools"])
        for p in new["permissions"]:
            if not isinstance(p, dict) or not str(p.get("tool") or "").strip():
                raise ValueError("every permission rule needs a tool (a name or a glob such as *)")
        registry.rules({"tools": {}}, [], new["permissions"])      # checks each rule's action
        if new["rag"]["type"] not in rag.TYPES:
            raise ValueError(f"rag type must be one of {', '.join(rag.TYPES)}")
        if not isinstance(new["rag"]["ocr"], bool):
            raise ValueError("rag ocr must be true or false")
        for key, value in [("k", new["rag"]["k"]), ("full_text_tokens", new["rag"]["full_text_tokens"]),
                           ("max_runs", new["loop"]["max_runs"]), ("max_repeats", new["loop"]["max_repeats"])]:
            lo, hi = LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a whole number from {lo} to {hi}, got {value!r}")

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        cfg = load()
        now = settings_now(cfg)
        if any(isinstance(now[k], dict) and not isinstance(body.get(k, {}), dict) for k in now):
            raise HTTPException(422, "rag and loop are objects, e.g. {\"rag\": {\"k\": 4}}")
        new = {k: ({**now[k], **body.get(k, {})} if isinstance(now[k], dict) else body.get(k, now[k])) for k in now}
        if not isinstance(new["permissions"], list):
            raise HTTPException(422, "permissions must be a list of {tool, pattern, action} rules")
        new["permissions"] = [{"tool": str(p.get("tool", "")).strip(), "pattern": str(p.get("pattern") or "*"),
                               "action": p.get("action")} if isinstance(p, dict) else p
                              for p in new["permissions"]]
        try:
            check(cfg, new)
        except ValueError as e:
            raise HTTPException(422, str(e))
        y = _yaml()
        wf_path, rt_path = Path(config), runtime_path(cfg)
        wf, rt = y.load(wf_path.read_text()), y.load(rt_path.read_text())
        wf["llm"]["actions"] = new["actions"]
        wf["review"] = new["review"]
        seq = wf["tools"]
        for i in reversed(range(len(seq))):          # edit the list in place, so the comments stay
            if seq[i] not in new["tools"]:
                del seq[i]
        for name in new["tools"]:
            if name not in seq:
                seq.append(name)
        rules = []
        for p in new["permissions"]:
            rule = CommentedMap(p)
            rule.fa.set_flow_style()
            rules.append(rule)
        wf["permissions"] = rules
        wf.setdefault("rag", CommentedMap()).update(new["rag"])
        wf["loop"].update(new["loop"])
        rt["roles"]["actor"]["model_ref"], rt["roles"]["reviewer"]["model_ref"] = new["actor"], new["reviewer"]
        for path, doc in ((wf_path, wf), (rt_path, rt)):
            with path.open("w") as f:
                y.dump(doc, f)
        return settings_now(load())

    # ---- meters -----------------------------------------------------------------------------------
    @app.get("/api/meters")
    def meters(session: str | None = None):
        cfg = load()
        known = {m.get("model"): m for m in (load_runtime(str(runtime_path(cfg))).get("models") or {}).values()}
        with closing(connect(cfg)) as conn:
            since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
            used = {r["model"]: r["used"] for r in conn.execute(
                "SELECT model, SUM(tokens) AS used FROM steps WHERE created_at >= ? AND model IS NOT NULL"
                " GROUP BY model", (since,))}
            last = conn.execute("SELECT model, tokens FROM steps WHERE session_id = ? AND tool IS NOT NULL"
                                " AND tokens > 0 ORDER BY id DESC LIMIT 1",
                                (find(conn, session),)).fetchone() if session else None
        context = None
        if last:
            spec = known.get(last["model"]) or {}
            sizes = [n for n in (spec.get("context_window"), spec.get("request_tokens")) if n]
            window = min(sizes) if sizes else None    # on a free tier the per-request limit is the real one
            context = {"model": last["model"], "used": last["tokens"], "window": window,
                       "context_window": spec.get("context_window"), "request_tokens": spec.get("request_tokens"),
                       "percent": round(100 * last["tokens"] / window, 1) if window else None}
        daily = [{"model": m, "used": used.get(m, 0), "cap": spec.get("daily_tokens"),
                  "percent": round(100 * used.get(m, 0) / spec["daily_tokens"], 1) if spec.get("daily_tokens") else None}
                 for m, spec in known.items()]
        daily += [{"model": m, "used": n, "cap": None, "percent": None} for m, n in used.items() if m not in known]
        return {"context": context, "daily": daily,
                "note": "agent usage recorded in the trace, last 24 hours; the provider's own count may differ"}

    # ---- the page ---------------------------------------------------------------------------------
    if (UI_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/")
        def not_built():
            return PlainTextResponse("The web UI is not built yet. Build it once (needs Node.js):\n\n"
                                     "    npm --prefix ui ci\n    npm --prefix ui run build\n\n"
                                     "then restart: python main.py serve\nThe API is up: /api/docs", status_code=503)
    return app


def serve(config: str = main.DEFAULT_CONFIG, host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    print(f"mini-agent web UI on http://{host}:{port}" + ("" if (UI_DIST / "index.html").exists()
          else "  (UI not built: npm --prefix ui ci && npm --prefix ui run build)"), flush=True)
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
