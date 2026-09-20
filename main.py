"""Run a workflow:  python main.py config/workflow.yaml "make an html page listing the first 10 primes"

The console shows one line per action; the full transcript (every prompt, reply and
observation) goes to sandbox/logs/workflow.log, and a session.json summary is written
into the workspace. Tools listed under `confirm:` in the YAML ask you before they run;
without a terminal they are refused unless you pass --yes.

If the agent is blocked, stuck repeating itself, or out of budget, you are asked for a
hint and the same session continues; an empty hint stops.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

import tools
from loop import run_workflow


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="workflow yaml")
    ap.add_argument("task", nargs="+", help="what the agent should do")
    ap.add_argument("--yes", action="store_true", help="approve confirm: tools without asking")
    a = ap.parse_args()
    task = " ".join(a.task)
    cfg = yaml.safe_load(Path(a.config).read_text())
    interactive = sys.stdin.isatty()

    log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(text):
        with log_file.open("a") as f:
            f.write(text + "\n")

    def confirm(tool, args):
        if a.yes:
            return True
        if not interactive:
            print(f"    {tool} needs confirmation but no terminal is attached (use --yes to allow)")
            return False
        for k, v in args.items():
            print(f"    {k}:\n" + "\n".join("      " + line for line in str(v).splitlines()[:30]))
        return input(f"    run {tool}? [y/N] ").strip().lower() == "y"

    print(f"{cfg['name']} · {cfg['llm']['model']}")
    print(f"task: {task}\n")
    log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} task={task!r}")
    result = run_workflow(cfg, task, log=log, show=print, confirm=confirm)

    reasons = {"max_runs": "out of budget", "blocked": "agent says it is blocked", "no_progress": "agent keeps repeating itself"}
    while result["status"] in reasons and interactive:
        print(f"\n{reasons[result['status']]} after {result['runs']} actions.")
        hint = input("hint for the agent (Enter to stop): ").strip()
        if not hint:
            break
        log(f"[escalate] hint: {hint}")
        print()
        result = run_workflow(cfg, task, log=log, show=print, confirm=confirm, previous=result, hint=hint)

    st = result["state"]
    files = tools.list_files({"workspace": result["workspace"]})
    print(f"\n{result['status']} · {result.get('runs', 0)} actions · {result['total_tokens']:,} tokens")
    if result["status"] == "llm_error":
        print(f"error:     {result['error']['message']}")
    print(f"answer:    {st['answer'] or '(none)'}")
    print(f"workspace: {result['workspace']}")
    print("files:     " + files.replace("\n", "\n           "))

    session = {"time": datetime.now().isoformat(timespec="seconds"), "workflow": cfg["name"],
               "model": cfg["llm"]["model"], "task": task, "status": result["status"],
               "actions": result.get("runs", 0), "total_tokens": result["total_tokens"],
               "answer": st["answer"], "files": files.splitlines()}
    (result["workspace"] / "session.json").write_text(json.dumps(session, indent=2, ensure_ascii=False))
    log(f"\n===== RESULT =====\n{json.dumps(session, indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
