"""Runs the steps from a workflow YAML, repeating up to loop.max_runs times.

Step types:
    act      the model replies with ONE json action {"tool": ..., "args": {...}};
             the tool runs and its observation goes into state
    llm      free-text model call, reply stored in state[save_as]
    stop_if  end the workflow if its `when` condition holds; result status is
             `status:` from the step (default "done")

Any step may carry `when: <condition>` and is skipped unless it holds.
`state` is one dict shared by all steps; prompt templates may use any key as {key}:
    {task} {run} {tools} {observation} {answer} {review} {files}

Callbacks: `log(text)` gets the full transcript, `show(text)` gets the short console
view, `confirm(tool, args) -> bool` is asked before tools listed under `confirm:`.
"""
import json
import re

import requests

import sandbox
import tools
from llm_handler import call_llm

JSON_FENCE = re.compile(r"```json[ \t]*\n(.*?)```", re.S)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(template: str, state: dict) -> str:
    """Fill {key} for keys present in state; leave anything else (like JSON examples) alone."""
    return PLACEHOLDER.sub(lambda m: str(state[m.group(1)]) if m.group(1) in state else m.group(0), template)


def parse_action(text: str) -> dict:
    """Last ```json block, or the outermost {...} if the model dropped the fence.
    Raises ValueError with a message meant to go back to the model as the observation."""
    blocks = JSON_FENCE.findall(text)
    raw = blocks[-1] if blocks else text[text.find("{"):text.rfind("}") + 1]
    if not raw:
        raise ValueError('no json action found; reply with ```json {"tool": ..., "args": {...}} ```')
    try:
        action = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid json: {e}")
    if not isinstance(action, dict) or "tool" not in action:
        raise ValueError('json must be an object with a "tool" key')
    action.setdefault("args", {})
    if not isinstance(action["args"], dict):
        raise ValueError('"args" must be an object')
    return action


def brief(value, width=70) -> str:
    """One-line preview of an argument or observation for the console."""
    s = str(value).replace("\n", " ⏎ ")
    return s if len(s) <= width else s[:width - 1] + "…"


CONDITIONS = {
    "answered": lambda s: s["answer"] != "",
    "review_pass": lambda s: s["review"].lstrip().upper().startswith("VERDICT: PASS"),
    "review_blocked": lambda s: s["review"].lstrip().upper().startswith("VERDICT: BLOCKED"),
}


def run_workflow(cfg: dict, task: str, log=lambda text: None, show=lambda text: None,
                 confirm=lambda tool, args: True, messages: list | None = None, workspace=None) -> dict:
    """Pass `messages` and `workspace` from a previous result to continue that session."""
    llm_cfg, sb_cfg, loop_cfg = cfg["llm"], cfg["sandbox"], cfg["loop"]
    allowed = cfg.get("tools", [])
    needs_confirm = cfg.get("confirm", [])
    workspace = workspace or sandbox.new_workspace(sb_cfg["dir"])
    ctx = {"workspace": workspace, "sandbox": sb_cfg}
    state = {"task": task, "run": 0, "tools": tools.describe(allowed),
             "observation": "", "answer": "", "review": "", "files": ""}
    if messages is None:
        messages = [{"role": "system", "content": render(cfg["prompts"]["system"], state)}]
    total_tokens = 0

    def ask(step, sid):
        nonlocal total_tokens
        template = step["prompt"]
        if state["run"] > 1 and "retry_prompt" in step:
            template = step["retry_prompt"]
        messages.append({"role": "user", "content": render(template, state)})
        res = call_llm(messages, model=llm_cfg["model"], temperature=llm_cfg["temperature"],
                       max_completion_tokens=llm_cfg["max_completion_tokens"])
        if res["ok"]:
            messages.append({"role": "assistant", "content": res["text"]})
            total_tokens += res["usage"].get("total_tokens", 0)
            log(f"[{sid}] tokens so far={total_tokens}\n{res['text']}")
        else:
            show(f"   llm error: {res['error']['message']}")
        return res

    def result(status, **extra):
        return {"status": status, "total_tokens": total_tokens, "state": state,
                "messages": messages, "workspace": workspace, **extra}

    def act(step, sid):
        state["answer"], state["review"] = "", ""
        try:
            action = parse_action(state["llm_text"])
            name, args = action["tool"], action["args"]
            show(f"{state['run']:>2}  {name}  " + "  ".join(f"{k}={brief(v, 60)}" for k, v in args.items()))
            if name == "final_answer":
                state["answer"] = str(args.get("answer", "")).strip() or "(empty answer)"
                obs = "final_answer recorded"
            elif name not in allowed:
                obs = f"unknown tool '{name}'; allowed: {', '.join(allowed)}, final_answer"
            elif name in needs_confirm and not confirm(name, args):
                obs = f"the user declined to run {name}"
            else:
                obs = tools.TOOLS[name](ctx, **args)
        except (ValueError, TypeError, OSError, requests.RequestException) as e:
            obs = f"error: {e}"
            show(f"{state['run']:>2}  (bad action)")
        state["observation"] = obs
        state["files"] = tools.snapshot(ctx)
        log(f"[{sid}] observation:\n{obs}")
        if obs != "final_answer recorded":
            show(f"    → {brief(obs)}")

    for run_no in range(1, loop_cfg["max_runs"] + 1):
        state["run"] = run_no
        log(f"\n===== run {run_no}/{loop_cfg['max_runs']} =====")

        for step in cfg["steps"]:
            kind, sid = step["type"], step.get("id", step["type"])
            if kind != "stop_if" and "when" in step and not CONDITIONS[step["when"]](state):
                continue

            if kind in ("act", "llm"):
                res = ask(step, sid)
                if not res["ok"]:
                    return result("llm_error", error=res["error"])
                state["llm_text"] = res["text"]
                if kind == "act":
                    act(step, sid)
                else:
                    state[step.get("save_as", "llm_text")] = res["text"]
                    first, _, rest = res["text"].strip().partition("\n")
                    show(f"    {sid} → {first.replace('VERDICT: ', '')}  {brief(rest.strip(), 60)}".rstrip())

            elif kind == "stop_if":
                if CONDITIONS[step["when"]](state):
                    log(f"[{sid}] '{step['when']}' holds, stopping")
                    return result(step.get("status", "done"), runs=run_no)
                log(f"[{sid}] '{step['when']}' does not hold")

            else:
                raise ValueError(f"unknown step type: {kind}")

    return result("max_runs", runs=loop_cfg["max_runs"])
