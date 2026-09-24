"""Sandbox: one workspace folder per agent session; run shell commands inside it.

    ws = new_workspace("sandbox")          -> sandbox/runs/run_001/
    run(command, ws) -> {"stdout", "stderr", "exit_code", "timed_out", "duration_ms", "exec_dir"}

Each execution is saved under ws/.exec/exec_NN.{sh,stdout.txt,stderr.txt,meta.json}
and logged as one line in sandbox/logs/sandbox.log. The command runs with cwd=ws,
so it sees the files the agent has put in the workspace.

This is a workspace, not isolation: a shell command runs with the same authority as the
user who started the agent. The permission gate in front of the bash tool is what stands
between the model and the machine. What we do enforce here is the environment: the child
never sees the secrets this project loads from .env, so `env` cannot print an API key.
"""
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

SHELL = "/bin/sh"
MAX_TIMEOUT_SEC = 120
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


def new_workspace(sandbox_dir="sandbox") -> Path:
    runs = Path(sandbox_dir) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    ws = runs / f"run_{len(list(runs.glob('run_*'))) + 1:03d}"
    ws.mkdir()
    return ws


def truncate(text: str, limit: int) -> str:
    """Keep head + tail so a runaway command cannot blow up the LLM context."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [truncated {len(text) - limit} chars] ...\n" + text[-half:]


def scrub_env(env: dict | None = None) -> dict:
    """The parent environment minus anything that looks like a secret."""
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not any(w in k.upper() for w in SECRET_WORDS)}


def _decode(b) -> str:
    # TimeoutExpired.stdout/.stderr are bytes or None even with text=True (see subprocess docs)
    return b.decode(errors="replace") if b else ""


def run(command: str, workspace: Path, timeout_sec=30, max_output_chars=4000) -> dict:
    exec_dir = workspace / ".exec"
    exec_dir.mkdir(exist_ok=True)
    n = len(list(exec_dir.glob("exec_*.sh"))) + 1
    script = exec_dir / f"exec_{n:02d}.sh"
    script.write_text(command)

    t0 = time.time()
    timed_out = False
    try:
        p = subprocess.run(
            [SHELL, "-c", command],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=scrub_env(),
        )
        stdout, stderr, exit_code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        stdout, stderr = _decode(e.stdout), _decode(e.stderr)
        exit_code, timed_out = -1, True
        stderr += f"\n[sandbox] killed after {timeout_sec}s timeout"
    duration_ms = int((time.time() - t0) * 1000)

    result = {
        "stdout": truncate(stdout, max_output_chars),
        "stderr": truncate(stderr, max_output_chars),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "exec_dir": str(script.with_suffix("")),
    }
    script.with_suffix(".stdout.txt").write_text(result["stdout"])
    script.with_suffix(".stderr.txt").write_text(result["stderr"])
    script.with_suffix(".meta.json").write_text(json.dumps({"command": command, **result}, indent=2))

    log = workspace.parent.parent / "logs" / "sandbox.log"   # workspace is <sandbox_dir>/runs/run_NNN
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {workspace.name}/{script.stem} "
                f"exit={exit_code} timed_out={timed_out} {duration_ms}ms {command[:80]!r}\n")
    return result


if __name__ == "__main__":
    # python sandbox.py
    ws = new_workspace()
    print(run("echo hello from sandbox", ws))
    print(run("sleep 5", ws, timeout_sec=1))
    print(run("python -c '1/0'", ws))
