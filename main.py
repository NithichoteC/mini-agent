"""Entry point:  python main.py config/workflow.yaml "make an html page listing the first 10 primes"

The console shows one line per action; the full transcript (every prompt, reply and
observation) goes to sandbox/logs/workflow.log. If the agent runs out of budget you are
asked for a hint and the session continues; an empty hint stops.
"""
import sys
from datetime import datetime
from pathlib import Path

import yaml

import tools
from loop import run_workflow


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cfg_path, task = sys.argv[1], " ".join(sys.argv[2:])
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    interactive = sys.stdin.isatty()

    log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(text):
        with log_file.open("a") as f:
            f.write(text + "\n")

    def confirm(tool, args):
        if not interactive:
            return True
        for k, v in args.items():
            print(f"    {k}:\n" + "\n".join("      " + line for line in str(v).splitlines()[:30]))
        return input(f"    run {tool}? [y/N] ").strip().lower() == "y"

    print(f"{cfg['name']} · {cfg['llm']['model']}")
    print(f"task: {task}\n")
    log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} task={task!r}")
    result = run_workflow(cfg, task, log=log, show=print, confirm=confirm)

    while result["status"] in ("max_runs", "blocked") and interactive:
        print(f"\n{'agent is blocked' if result['status'] == 'blocked' else 'out of budget'} after {result['runs']} actions.")
        hint = input("hint for the agent (Enter to stop): ").strip()
        if not hint:
            break
        log(f"[escalate] hint: {hint}")
        print()
        result = run_workflow(cfg, f"Hint from the user: {hint}\nContinue the original task.",
                              log=log, show=print, confirm=confirm,
                              messages=result["messages"], workspace=result["workspace"])

    st = result["state"]
    print(f"\n{result['status']} · {result.get('runs', 0)} actions · {result['total_tokens']:,} tokens")
    if result["status"] == "llm_error":
        print(f"error:     {result['error']['message']}")
    print(f"answer:    {st['answer'] or '(none)'}")
    print(f"workspace: {result['workspace']}")
    files = tools.list_files({"workspace": result["workspace"]})
    print("files:     " + files.replace("\n", "\n           "))
    log(f"\n===== RESULT ===== {result['status']} runs={result.get('runs')} tokens={result['total_tokens']}\n"
        f"answer: {st['answer']}\nfiles:\n{files}")


if __name__ == "__main__":
    main()
