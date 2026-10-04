"""mini-agent command line.

    python main.py run "make an html page listing the first 10 primes"
    python main.py run "..." --format json      one JSON event per line on stdout, for scripts
    python main.py run "summarise it" --file report.pdf     attach a file (repeat --file for more)
    python main.py chat                         talk back and forth; each message is one more turn;
                                                /attach <path> adds a file to your next message
    python main.py run "now make it blue" -c    one more message to the newest session (-s <id>: another)
    python main.py export [session]             a session as JSON - the same as its transcript.json
    python main.py tools                        the tool registry and each tool's permission
    python main.py trace                        recent sessions
    python main.py trace run_003                every step of one session (any unique part of its id)
    python main.py trace --last                 the newest session
    python main.py trace --tools                how often each tool was used, allowed and refused

run: progress, tool lines and prompts go to stderr and the final answer to stdout, so
`python main.py run "..." > answer.md` keeps a clean answer while you watch (opencode's split).
Exit code 0 means the reviewer passed it; 1 means anything else. The full transcript goes to
sandbox/logs/workflow.log, every action and its permission decision to the trace database
(sandbox/trace.db), and a session.json summary into the workspace.

A tool whose permission is "ask" (config/tools.json, plus `permissions:` in the yaml) prompts
before it runs: y once, a to always allow it for this session, n to decline (with an optional
reason, which goes back to the agent). Without a terminal these are refused unless --yes.
If the agent is blocked, repeating itself, or out of budget, you are asked for a hint and the
same session continues; an empty hint stops.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

import registry
import tools
import tracedb
from loop import brief, model_name, run_workflow

DEFAULT_CONFIG = "config/workflow.yaml"
STYLE = {"dim": "\x1b[90m", "bold": "\x1b[1m", "red": "\x1b[91m", "green": "\x1b[92m",
         "yellow": "\x1b[93m", "cyan": "\x1b[96m", "reset": "\x1b[0m"}   # opencode's cli/ui.ts palette


def color() -> bool:
    """Checked per call, so a redirected or captured stderr never receives escape codes."""
    return sys.stderr.isatty() and not os.getenv("NO_COLOR")


def paint(text: str, style: str) -> str:
    return f"{STYLE[style]}{text}{STYLE['reset']}" if color() else text


def ui(text: str = "") -> None:
    """Everything meant for the person watching goes to stderr; stdout carries only the result."""
    print(text, file=sys.stderr, flush=True)


def ask_user(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def trace_path(cfg: dict) -> str:
    return cfg.get("trace", {}).get("path", "sandbox/trace.db")


def setup(a) -> dict:
    """Everything one CLI session needs: the config, the trace, and the callbacks for run_workflow."""
    cfg = load_config(a.config)
    if getattr(a, "model", None):
        cfg["llm"] = {**cfg["llm"], "model": a.model}
    as_json = getattr(a, "format", "default") == "json"
    interactive = sys.stdin.isatty()
    trace_db = tracedb.connect(trace_path(cfg)) if cfg.get("trace", {}).get("enabled", True) else None
    reg = registry.load(cfg.get("registry", registry.DEFAULT_PATH))
    log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(text):
        with log_file.open("a") as f:
            f.write(text + "\n")

    def show(text):
        if as_json:
            return
        if text.lstrip().startswith("\u2192"):
            text = paint(text, "dim")
        elif "review \u2192 PASS" in text:
            text = text.replace("PASS", paint("PASS", "green"), 1)
        elif "review \u2192" in text:
            text = text.replace("review \u2192 ", "review \u2192 " + STYLE["red"] * color(), 1) + STYLE["reset"] * color()
        ui(text)

    def emit(event):
        if as_json:
            print(json.dumps(event, ensure_ascii=False, default=str), flush=True)

    def confirm(tool, args):
        if a.yes:
            return "allow", ""
        if not interactive:
            ui(paint(f"    {tool} needs confirmation but no terminal is attached (use --yes to allow)", "yellow"))
            return "deny", "no terminal is attached"
        for k, v in args.items():
            ui(f"    {k}:\n" + "\n".join("      " + line for line in str(v).splitlines()[:30]))
        scope = registry.always_scope(reg, tool, args)
        always = "always" if scope == ["*"] else f"always for {', '.join(p for p in scope if not p.endswith(' *'))}"
        while True:
            choice = ask_user(paint(f"    run {tool}? ", "yellow") + f"[y] once  [a] {always}  [n] reject: ").lower()
            if choice == "y":
                return "allow", ""
            if choice == "a":
                return "always", ""
            if choice in ("n", ""):
                return "deny", ask_user("    reason (optional, sent back to the agent): ")
            ui("    please answer y, a or n")

    return {"cfg": cfg, "as_json": as_json, "interactive": interactive, "trace_db": trace_db, "log": log,
            "emit": emit, "model": model_name(cfg),
            "kw": {"log": log, "show": show, "confirm": confirm, "trace_db": trace_db, "emit": emit}}


def resume(env: dict, which: str | None) -> dict:
    """A past session, ready to be passed to run_workflow as `previous`: the newest one when
    `which` is None (--continue), else any unique part of a session id (--session run_003)."""
    if env["trace_db"] is None:
        sys.exit("continuing a session needs the trace (trace.enabled: true) - the transcript is built from it")
    try:
        sid = tracedb.find(env["trace_db"], which) if which else tracedb.last_session(env["trace_db"])
    except ValueError as e:
        sys.exit(str(e))
    if sid is None:
        sys.exit("no session to continue yet")
    path = Path(tracedb.session(env["trace_db"], sid)["workspace"]) / "transcript.json"
    if not path.exists():
        sys.exit(f"session {sid} has no {path.name} (it was run before sessions could be continued)")
    saved = json.loads(path.read_text())["resume"]
    return {"workspace": path.parent, "messages": saved["messages"], "state": saved["state"],
            "total_tokens": saved["total_tokens"], "trace_id": sid, "status": saved.get("status")}


def turn(env: dict, text: str, previous: dict | None, attachments=()) -> dict:
    """One user message, worked until the agent answers - asking for hints if it gets stuck."""
    cfg, kw = env["cfg"], env["kw"]
    env["log"](f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} "
               f"{'turn' if previous else 'task'}={text!r}" + "".join(f" file={f}" for f in attachments))
    result = run_workflow(cfg, text, previous=previous, attachments=attachments, **kw)
    reasons = {"max_runs": "out of budget", "blocked": "the agent says it is blocked",
               "no_progress": "the agent repeated the same action {n} times (a doom loop)"}
    while result["status"] in reasons and env["interactive"] and not env["as_json"]:
        why = reasons[result["status"]].format(n=result["state"].get("max_repeats", 3))
        ui(paint(f"\n{why} after {result['runs']} actions.", "yellow"))
        hint = ask_user("continue with a hint for the agent (Enter to stop): ")
        if not hint:
            break
        env["log"](f"[escalate] hint: {hint}")
        ui()
        result = run_workflow(cfg, text, previous=result, hint=hint, **kw)
    return result


def finish(env: dict, result: dict, text: str, started: float) -> int:
    """After every turn: session.json, transcript.json (conversation + what is needed to resume
    it), the footer on stderr and the answer on stdout. Returns the exit code."""
    cfg, st, elapsed = env["cfg"], result["state"], time.time() - started
    files = tools.glob({"workspace": result["workspace"]})
    session = {"time": datetime.now().isoformat(timespec="seconds"), "workflow": cfg["name"],
               "model": env["model"], "task": text, "status": result["status"],
               "actions": result.get("runs", 0), "total_tokens": result["total_tokens"],
               "seconds": round(elapsed, 1), "answer": st["answer"], "files": files.splitlines(),
               "workspace": str(result["workspace"]), "trace_id": result["trace_id"]}
    ws = Path(result["workspace"])
    (ws / "session.json").write_text(json.dumps(session, indent=2, ensure_ascii=False))
    if env["trace_db"] is not None:
        doc = tracedb.transcript(env["trace_db"], result["trace_id"])
        doc["resume"] = {"status": result["status"], "total_tokens": result["total_tokens"],
                         "state": st, "messages": result["messages"]}
        (ws / "transcript.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str))
    env["log"](f"\n===== RESULT =====\n{json.dumps(session, indent=2, ensure_ascii=False)}")

    if env["as_json"]:
        env["emit"]({"type": "session.end", **session, **({"error": result["error"]} if "error" in result else {})})
    else:
        status = paint(result["status"], "green" if result["status"] == "done" else "red")
        ui(f"\n{paint(chr(0x25a3), 'cyan')} {status} \u00b7 {result.get('runs', 0)} actions \u00b7 "
           f"{result['total_tokens']:,} tokens \u00b7 {elapsed:.1f}s")
        if result["status"] == "llm_error":
            ui(paint(f"  error: {result['error']['message']}", "red"))
        ui(paint(f"  workspace {result['workspace']}", "dim"))
        ui(paint("  files     " + files.replace("\n", "\n            "), "dim"))
        if result["trace_id"]:
            ui(paint(f"  session   {result['trace_id']}  (transcript.json in the workspace)", "dim"))
        ui()
        print(st["answer"] or "(no answer)", flush=True)          # the one thing on stdout
    return 0 if result["status"] == "done" else 1


def files(paths) -> list[str]:
    missing = [p for p in paths or [] if not Path(p).expanduser().is_file()]
    if missing:
        sys.exit(f"no such file: {', '.join(missing)}")
    return list(paths or [])


def cmd_run(a) -> int:
    text = " ".join(a.task)
    attached = files(a.file)
    env = setup(a)
    previous = resume(env, a.session) if (a.continue_ or a.session) else None
    if not env["as_json"]:
        ui(paint("> ", "cyan") + paint(f"{env['cfg']['name']} \u00b7 {env['model']}", "bold"))
        if previous:
            ui(paint(f"  continuing {previous['trace_id']}", "dim"))
        ui(paint(f"  task: {text}", "dim") + "\n")
    started = time.time()
    return finish(env, turn(env, text, previous, attached), text, started)


def cmd_chat(a) -> int:
    """Talk to the agent: each message is one turn in the same session, workspace and conversation."""
    pending = files(a.file)
    env = setup(a)
    previous = resume(env, a.session) if (a.continue_ or a.session) else None
    ui(paint("> ", "cyan") + paint(f"{env['cfg']['name']} \u00b7 {env['model']}", "bold"))
    ui(paint(f"  continuing {previous['trace_id']}" if previous else "  new session", "dim")
       + paint("  \u00b7  /attach <path> adds a file  \u00b7  an empty line or 'exit' ends the chat", "dim"))
    code = 0
    while True:
        try:
            text = ask_user(paint("\n> ", "cyan"))
        except EOFError:
            break
        if text.lower() in ("", "exit", "quit", "/exit"):
            break
        if text.startswith("/attach"):
            path = text[len("/attach"):].strip().strip("'\"")
            if Path(path).expanduser().is_file():
                pending.append(path)
                ui(paint(f"  {Path(path).name} goes with your next message", "dim"))
            else:
                ui(paint(f"  no such file: {path or '(give a path)'}", "yellow"))
            continue
        started = time.time()
        result = turn(env, text, previous, pending)
        pending = []
        code = finish(env, result, text, started)
        previous = result
    return code


def cmd_export(a) -> int:
    """opencode's `export`: one session as JSON on stdout (the newest when no id is given)."""
    cfg = load_config(a.config)
    path = trace_path(cfg)
    if not Path(path).exists():
        sys.exit(f"no trace database at {path} yet - run a task first")
    conn = tracedb.connect(path)
    try:
        sid = tracedb.find(conn, a.session) if a.session else tracedb.last_session(conn)
        if sid is None:
            sys.exit("no sessions traced yet")
        print(json.dumps(tracedb.transcript(conn, sid), indent=2, ensure_ascii=False))
    except ValueError as e:
        sys.exit(str(e))
    return 0


def cmd_tools(a) -> int:
    cfg = load_config(a.config)
    reg = registry.load(cfg.get("registry", registry.DEFAULT_PATH))
    enabled = cfg.get("tools", [])
    rules = registry.rules(reg, [n for n in reg["tools"]], cfg.get("permissions", []))
    rows = [{"tool": name, "enabled": name in enabled, "permission": registry.decide(rules, name, "*"),
             "signature": registry.signature(reg, name), "description": spec["description"]}
            for name, spec in reg["tools"].items()]
    if a.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    width = max(len(r["signature"]) for r in rows)
    print(f"{'':2}{'tool':<{width}}  {'permission':<10}  description")
    for r in rows:
        mark = paint("\u25cf", "green") if r["enabled"] else paint("\u25cb", "dim")
        perm = paint(f"{r['permission']:<10}", {"allow": "green", "ask": "yellow", "deny": "red"}[r["permission"]])
        print(f"{mark} {r['signature']:<{width}}  {perm}  {brief(r['description'], 70)}")
    extra = [x for x in cfg.get("permissions", [])]
    print(paint(f"\n\u25cf enabled in {a.config}   permission shown for pattern '*'"
                + (f"; {len(extra)} override rule(s) in permissions:" if extra else ""), "dim"))
    return 0


def show_sessions(rows: list[dict]) -> None:
    if not rows:
        print("no sessions traced yet")
        return
    width = max(len(r["id"]) for r in rows)
    print(f"{'session':<{width}}  {'status':<12} {'runs':>4} {'tokens':>7}  task")
    for r in rows:
        print(f"{r['id']:<{width}}  {r['status'] or 'running':<12} {r['runs'] or 0:>4} "
              f"{r['total_tokens'] or 0:>7,}  {brief(r['task'], 60)}")


def show_steps(session: dict, rows: list[dict], reg: dict) -> None:
    print(f"session   {session['id']}")
    print(f"task      {session['task']}")
    print(f"status    {session['status'] or 'running'} · {session['runs'] or 0} runs · "
          f"{session['total_tokens'] or 0:,} tokens · {session['model']}")
    print(f"workspace {session['workspace']}\n")
    print(f"{'run':>3}  {'step':<7} {'decision':<12} action")
    for r in rows:
        if r["tool"] is None:                       # a plain llm step, e.g. the review
            first = (r["model_output"] or r["observation"] or "").strip().splitlines()[:1]
            print(f"{r['run']:>3}  {r['step_id']:<7} {'':<12} {brief(first[0] if first else '', 90)}")
            continue
        if r["tool"] == "final_answer":
            icon, title = "▸", f"Final answer: {brief((r['args'] or {}).get('answer', ''), 70)}"
        elif r["tool"] in reg["tools"] and isinstance(r["args"], dict):
            icon, title = registry.render(reg, r["tool"], r["args"])
        else:
            icon, title = "✗", f"{r['tool']} {brief(r['args'] or '', 60)}"
        print(f"{r['run']:>3}  {r['step_id']:<7} {r['decision']:<12} {icon} {brief(title, 90)}")
        if r["tool"] != "final_answer":
            print(f"{'':>3}  {'':<7} {'':<12}   → {brief(r['observation'], 90)}")


def cmd_trace(a) -> int:
    cfg = load_config(a.config)
    path = trace_path(cfg)
    if not Path(path).exists():
        sys.exit(f"no trace database at {path} yet - run a task first")
    conn = tracedb.connect(path)
    as_json = a.format == "json"

    if a.tools:
        rows = tracedb.tool_usage(conn)
        if as_json:
            print(json.dumps(rows, indent=2))
            return
        print(f"{'tool':<12} {'decision':<12} {'calls':>5}")
        for r in rows:
            print(f"{r['tool']:<12} {r['decision']:<12} {r['calls']:>5}")
        return

    if a.session or a.last:
        try:
            sid = tracedb.last_session(conn) if a.last else tracedb.find(conn, a.session)
        except ValueError as e:
            sys.exit(str(e))
        if sid is None:
            sys.exit("no sessions traced yet")
        session = tracedb.session(conn, sid)
        rows = tracedb.steps(conn, sid)
        if as_json:
            print(json.dumps({"session": session, "steps": rows}, indent=2, ensure_ascii=False))
            return
        show_steps(session, rows, registry.load(cfg.get("registry", registry.DEFAULT_PATH)))
        return

    rows = tracedb.sessions(conn, limit=a.limit)
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    show_sessions(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    cfg_help = f"workflow yaml (default {DEFAULT_CONFIG})"

    run = sub.add_parser("run", help="give the agent a task (or one more message with --continue)")
    run.add_argument("task", nargs="+", help="what the agent should do")
    run.add_argument("--format", choices=("default", "json"), default="default",
                     help="json: one event per line on stdout instead of the formatted view")

    chat = sub.add_parser("chat", help="talk to the agent: back and forth in one session")
    for p in (run, chat):
        p.add_argument("--config", default=DEFAULT_CONFIG, help=cfg_help)
        p.add_argument("--model", help="a models: key from runtime.yaml or a raw model id, for the actor")
        p.add_argument("--yes", action="store_true", help="approve every 'ask' tool without prompting")
        p.add_argument("-c", "--continue", dest="continue_", action="store_true", help="continue the newest session")
        p.add_argument("-s", "--session", help="continue this session (any unique part of its id)")
        p.add_argument("--file", action="append", metavar="PATH",
                       help="attach a file to the (first) message; short files go in whole, long ones are indexed")
    run.set_defaults(func=cmd_run)
    chat.set_defaults(func=cmd_chat)

    ex = sub.add_parser("export", help="one session as JSON: every message, tool call and verdict")
    ex.add_argument("session", nargs="?", help="a session id or any unique part of one (default: the newest)")
    ex.add_argument("--config", default=DEFAULT_CONFIG, help=cfg_help)
    ex.set_defaults(func=cmd_export)

    tl = sub.add_parser("tools", help="list the tool registry and permissions")
    tl.add_argument("--config", default=DEFAULT_CONFIG, help=cfg_help)
    tl.add_argument("--format", choices=("default", "json"), default="default")
    tl.set_defaults(func=cmd_tools)

    tr = sub.add_parser("trace", help="read the trace database")
    tr.add_argument("session", nargs="?", help="a session id, or any unique part of one (e.g. run_003)")
    tr.add_argument("--last", action="store_true", help="the newest session")
    tr.add_argument("--tools", action="store_true", help="calls per tool and permission decision")
    tr.add_argument("--limit", type=int, default=20, help="sessions to list (default 20)")
    tr.add_argument("--format", choices=("default", "json"), default="default")
    tr.add_argument("--config", default=DEFAULT_CONFIG, help=cfg_help)
    tr.set_defaults(func=cmd_trace)

    a = ap.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
