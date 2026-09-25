"""mini-agent command line.

    python main.py run "make an html page listing the first 10 primes"
    python main.py trace                 recent sessions
    python main.py trace run_003         every step of one session (any unique part of its id)
    python main.py trace --last          the newest session
    python main.py trace --tools         how often each tool was used, allowed and refused

run: the console shows one line per action; the full transcript (every prompt, reply and
observation) goes to sandbox/logs/workflow.log, every action and its permission decision goes
to the trace database (sandbox/trace.db), and a session.json summary is written into the
workspace. A tool whose permission is "ask" (config/tools.json, plus the `permissions:`
overrides in the yaml) prompts you before it runs: y once, a to always allow it for the rest
of this session, or n to decline (you can give a reason, which goes back to the agent).
Without a terminal these are refused unless you pass --yes. If the agent is blocked, stuck
repeating itself, or out of budget, you are asked for a hint and the same session continues;
an empty hint stops.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

import registry
import tools
import tracedb
from loop import brief, model_name, run_workflow

DEFAULT_CONFIG = "config/workflow.yaml"


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def trace_path(cfg: dict) -> str:
    return cfg.get("trace", {}).get("path", "sandbox/trace.db")


def cmd_run(a) -> None:
    task = " ".join(a.task)
    cfg = load_config(a.config)
    interactive = sys.stdin.isatty()
    trace_db = tracedb.connect(trace_path(cfg)) if cfg.get("trace", {}).get("enabled", True) else None

    log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(text):
        with log_file.open("a") as f:
            f.write(text + "\n")

    def confirm(tool, args):
        if a.yes:
            return "allow", ""
        if not interactive:
            print(f"    {tool} needs confirmation but no terminal is attached (use --yes to allow)")
            return "deny", "no terminal is attached"
        for k, v in args.items():
            print(f"    {k}:\n" + "\n".join("      " + line for line in str(v).splitlines()[:30]))
        while True:
            choice = input(f"    run {tool}? [y] once  [a] always  [n] reject: ").strip().lower()
            if choice == "y":
                return "allow", ""
            if choice == "a":
                return "always", ""
            if choice in ("n", ""):
                reason = input("    reason (optional, sent back to the agent): ").strip()
                return "deny", reason
            print("    please answer y, a or n")

    print(f"{cfg['name']} · {model_name(cfg)}")
    print(f"task: {task}\n")
    log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} task={task!r}")
    kw = {"log": log, "show": print, "confirm": confirm, "trace_db": trace_db}
    result = run_workflow(cfg, task, **kw)

    reasons = {"max_runs": "out of budget", "blocked": "agent says it is blocked", "no_progress": "agent keeps repeating itself"}
    while result["status"] in reasons and interactive:
        print(f"\n{reasons[result['status']]} after {result['runs']} actions.")
        hint = input("hint for the agent (Enter to stop): ").strip()
        if not hint:
            break
        log(f"[escalate] hint: {hint}")
        print()
        result = run_workflow(cfg, task, previous=result, hint=hint, **kw)

    st = result["state"]
    files = tools.glob({"workspace": result["workspace"]})
    print(f"\n{result['status']} · {result.get('runs', 0)} actions · {result['total_tokens']:,} tokens")
    if result["status"] == "llm_error":
        print(f"error:     {result['error']['message']}")
    print(f"answer:    {st['answer'] or '(none)'}")
    print(f"workspace: {result['workspace']}")
    print("files:     " + files.replace("\n", "\n           "))
    if result["trace_id"]:
        print(f"trace:     python main.py trace {result['trace_id']}")

    session = {"time": datetime.now().isoformat(timespec="seconds"), "workflow": cfg["name"],
               "model": model_name(cfg), "task": task, "status": result["status"],
               "actions": result.get("runs", 0), "total_tokens": result["total_tokens"],
               "answer": st["answer"], "files": files.splitlines(), "trace_id": result["trace_id"]}
    (result["workspace"] / "session.json").write_text(json.dumps(session, indent=2, ensure_ascii=False))
    log(f"\n===== RESULT =====\n{json.dumps(session, indent=2, ensure_ascii=False)}")


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


def cmd_trace(a) -> None:
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

    run = sub.add_parser("run", help="give the agent a task")
    run.add_argument("task", nargs="+", help="what the agent should do")
    run.add_argument("--config", default=DEFAULT_CONFIG, help=f"workflow yaml (default {DEFAULT_CONFIG})")
    run.add_argument("--yes", action="store_true", help="approve every 'ask' tool without prompting")
    run.set_defaults(func=cmd_run)

    tr = sub.add_parser("trace", help="read the trace database")
    tr.add_argument("session", nargs="?", help="a session id, or any unique part of one (e.g. run_003)")
    tr.add_argument("--last", action="store_true", help="the newest session")
    tr.add_argument("--tools", action="store_true", help="calls per tool and permission decision")
    tr.add_argument("--limit", type=int, default=20, help="sessions to list (default 20)")
    tr.add_argument("--format", choices=("default", "json"), default="default")
    tr.add_argument("--config", default=DEFAULT_CONFIG, help=f"workflow yaml (default {DEFAULT_CONFIG})")
    tr.set_defaults(func=cmd_trace)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
