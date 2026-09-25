"""mini-agent command line.

    python main.py run "make an html page listing the first 10 primes"
    python main.py run "..." --format json      one JSON event per line on stdout, for scripts
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


def cmd_run(a) -> int:
    task = " ".join(a.task)
    cfg = load_config(a.config)
    if a.model:
        cfg["llm"] = {**cfg["llm"], "model": a.model}
    as_json = a.format == "json"
    interactive = sys.stdin.isatty()
    trace_db = tracedb.connect(trace_path(cfg)) if cfg.get("trace", {}).get("enabled", True) else None
    started = time.time()

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
        while True:
            choice = ask_user(paint(f"    run {tool}? ", "yellow") + "[y] once  [a] always  [n] reject: ").lower()
            if choice == "y":
                return "allow", ""
            if choice == "a":
                return "always", ""
            if choice in ("n", ""):
                return "deny", ask_user("    reason (optional, sent back to the agent): ")
            ui("    please answer y, a or n")

    model = model_name(cfg)
    if not as_json:
        ui(paint("> ", "cyan") + paint(f"{cfg['name']} \u00b7 {model}", "bold"))
        ui(paint(f"  task: {task}", "dim") + "\n")
    log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} task={task!r}")
    kw = {"log": log, "show": show, "confirm": confirm, "trace_db": trace_db, "emit": emit}
    result = run_workflow(cfg, task, **kw)

    reasons = {"max_runs": "out of budget", "blocked": "the agent says it is blocked",
               "no_progress": "the agent repeated the same action {n} times (a doom loop)"}
    while result["status"] in reasons and interactive and not as_json:
        why = reasons[result["status"]].format(n=result["state"].get("max_repeats", 3))
        ui(paint(f"\n{why} after {result['runs']} actions.", "yellow"))
        hint = ask_user("continue with a hint for the agent (Enter to stop): ")
        if not hint:
            break
        log(f"[escalate] hint: {hint}")
        ui()
        result = run_workflow(cfg, task, previous=result, hint=hint, **kw)

    st, elapsed = result["state"], time.time() - started
    files = tools.glob({"workspace": result["workspace"]})
    session = {"time": datetime.now().isoformat(timespec="seconds"), "workflow": cfg["name"],
               "model": model, "task": task, "status": result["status"],
               "actions": result.get("runs", 0), "total_tokens": result["total_tokens"],
               "seconds": round(elapsed, 1), "answer": st["answer"], "files": files.splitlines(),
               "workspace": str(result["workspace"]), "trace_id": result["trace_id"]}
    (result["workspace"] / "session.json").write_text(json.dumps(session, indent=2, ensure_ascii=False))
    log(f"\n===== RESULT =====\n{json.dumps(session, indent=2, ensure_ascii=False)}")

    if as_json:
        emit({"type": "session.end", **session, **({"error": result["error"]} if "error" in result else {})})
    else:
        status = paint(result["status"], "green" if result["status"] == "done" else "red")
        ui(f"\n{paint(chr(0x25a3), 'cyan')} {status} \u00b7 {result.get('runs', 0)} actions \u00b7 "
           f"{result['total_tokens']:,} tokens \u00b7 {elapsed:.1f}s")
        if result["status"] == "llm_error":
            ui(paint(f"  error: {result['error']['message']}", "red"))
        ui(paint(f"  workspace {result['workspace']}", "dim"))
        ui(paint("  files     " + files.replace("\n", "\n            "), "dim"))
        if result["trace_id"]:
            ui(paint(f"  trace     python main.py trace {result['trace_id']}", "dim"))
        ui()
        print(st["answer"] or "(no answer)")          # the one thing on stdout
    return 0 if result["status"] == "done" else 1


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

    run = sub.add_parser("run", help="give the agent a task")
    run.add_argument("task", nargs="+", help="what the agent should do")
    run.add_argument("--config", default=DEFAULT_CONFIG, help=cfg_help)
    run.add_argument("--model", help="a models: key from runtime.yaml or a raw model id, for the actor")
    run.add_argument("--yes", action="store_true", help="approve every 'ask' tool without prompting")
    run.add_argument("--format", choices=("default", "json"), default="default",
                     help="json: one event per line on stdout instead of the formatted view")
    run.set_defaults(func=cmd_run)

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
