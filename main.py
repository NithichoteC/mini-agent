"""Entry point:  python main.py config/workflow.yaml "make an html page listing the first 10 primes"

Logs everything to stdout AND sandbox/logs/workflow.log.
If the agent runs out of budget (max_runs) you are asked for a hint; the conversation
continues with a fresh budget. Empty hint = stop.
"""
import json
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

    log_file = Path(cfg["sandbox"]["dir"]) / "logs" / "workflow.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str):
        print(msg)
        with log_file.open("a") as f:
            f.write(msg + "\n")

    log(f"\n##### {datetime.now().isoformat(timespec='seconds')} workflow={cfg['name']} task={task!r}")
    result = run_workflow(cfg, task, log=log)

    while result["status"] == "max_runs" and sys.stdin.isatty():
        log(f"\n[escalate] out of budget after {result['runs']} runs. last observation:\n{result['state']['observation']}")
        hint = input("hint for the agent (Enter to stop): ").strip()
        if not hint:
            break
        log(f"[escalate] hint: {hint}")
        result = run_workflow(cfg, f"Hint from the user: {hint}\nContinue the original task.", log=log,
                              messages=result["messages"], workspace=result["workspace"])

    st = result["state"]
    summary = {k: v for k, v in result.items() if k not in ("state", "messages", "workspace")}
    log(f"\n===== RESULT =====\n{json.dumps(summary, indent=2)}")
    log(f"answer:    {st['answer'] or '(none)'}")
    log(f"workspace: {result['workspace']}")
    log(f"files:\n{tools.list_files({'workspace': result['workspace']})}")


if __name__ == "__main__":
    main()
