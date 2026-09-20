"""Runs the steps from a workflow YAML, repeating up to loop.max_runs times.

Step types:
    act      the model replies with ONE json action {"tool": ..., "args": {...}};
             the tool runs and its observation goes into state
    llm      free-text model call, reply stored in state[save_as]. With `system:` it runs
             in its own fresh conversation (a separate role, e.g. the reviewer) instead of
             the agent's (`system:` names an entry under prompts: or is literal text);
             `model:` overrides the model for that step
    stop_if  end the workflow if its `when` condition holds; result status is
             `status:` from the step (default "done")

Any step may carry `when: <condition>` and is skipped unless it holds.
`state` is one dict shared by all steps; prompt templates may use any key as {key}:
    {task} {hint} {run} {tools} {observation} {answer} {review} {files} {repeats}

Callbacks: `log(text)` gets the full transcript, `show(text)` the short console view,
`confirm(tool, args) -> bool` is asked before tools listed under `confirm:` (default: deny).
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
    # accept both {"tool": t, "args": {...}} and the flat {"tool": t, "path": ..., ...}
    args = action.get("args")
    if args is None:
        args = {k: v for k, v in action.items() if k != "tool"}
    if not isinstance(args, dict):
        raise ValueError('"args" must be an object')
    return {"tool": action["tool"], "args": args}


def brief(value, width=70) -> str:
    """One-line preview of an argument or observation for the console."""
    s = str(value).replace("\n", " ⏎ ")
    return s if len(s) <= width else s[:width - 1] + "…"


CONDITIONS = {
    "answered": lambda s: s["answer"] != "",
    "review_pass": lambda s: s["review"].lstrip().upper().startswith("VERDICT: PASS"),
    "review_blocked": lambda s: s["review"].lstrip().upper().startswith("VERDICT: BLOCKED"),
    "no_progress": lambda s: s["repeats"] >= s["max_repeats"],
}


def run_workflow(cfg: dict, task: str, log=lambda text: None, show=lambda text: None,
                 confirm=lambda tool, args: False, previous: dict | None = None, hint: str = "") -> dict:
    """Pass a previous result as `previous` (plus a `hint`) to continue that session:
    same conversation, workspace and token count; the task stays the original one."""
    llm_cfg, sb_cfg, loop_cfg = cfg["llm"], cfg["sandbox"], cfg["loop"]
    allowed = cfg.get("tools", [])
    needs_confirm = cfg.get("confirm", [])

    if previous:
        workspace, messages, total_tokens = previous["workspace"], previous["messages"], previous["total_tokens"]
        state = {**previous["state"], "hint": hint, "answer": "", "review": "", "repeats": 0}
    else:
        workspace = sandbox.new_workspace(sb_cfg["dir"])
        messages, total_tokens = [], 0
        state = {"task": task, "hint": "", "run": 0, "tools": tools.describe(allowed),
                 "observation": "", "answer": "", "review": "", "files": "",
                 "repeats": 0, "max_repeats": loop_cfg.get("max_repeats", 3), "last_action": None}
        messages.append({"role": "system", "content": render(cfg["prompts"]["system"], state)})
    ctx = {"workspace": workspace, "sandbox": sb_cfg}

    def ask(step, sid):
        nonlocal total_tokens
        template = step["prompt"]
        if state["run"] == 1 and state["hint"] and "hint_prompt" in step:
            template = step["hint_prompt"]
        elif state["run"] > 1 and "retry_prompt" in step:
            template = step["retry_prompt"]
        user = {"role": "user", "content": render(template, state)}
        if "system" in step:   # separate conversation for this role; names a prompts: entry or is literal text
            system = cfg["prompts"].get(step["system"], step["system"])
            convo = [{"role": "system", "content": render(system, state)}, user]
        else:
            messages.append(user)
            convo = messages
        res = call_llm(convo, model=step.get("model", llm_cfg["model"]), temperature=llm_cfg["temperature"],
                       max_completion_tokens=llm_cfg["max_completion_tokens"])
        if res["ok"]:
            if convo is messages:
                messages.append({"role": "assistant", "content": res["text"]})
            total_tokens += res["usage"].get("total_tokens", 0)
            log(f"[{sid}] tokens so far={total_tokens}\n{res['text']}")
        else:
            show(f"    llm error: {res['error']['message']}")
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
            fingerprint = json.dumps(action, sort_keys=True)
            state["repeats"] = state["repeats"] + 1 if fingerprint == state["last_action"] else 0
            state["last_action"] = fingerprint
            if name == "final_answer":
                state["answer"] = str(args.get("answer", "")).strip() or "(empty answer)"
                obs = "final_answer recorded"
            elif name not in allowed:
                obs = f"unknown tool '{name}'; allowed: {', '.join(allowed)}, final_answer"
            elif name in needs_confirm and not confirm(name, args):
                obs = f"the user declined to run {name}"
            else:
                try:
                    obs = tools.TOOLS[name](ctx, **args)
                except TypeError as e:   # wrong argument names: tell the model the signature
                    obs = f"error: {e}. usage: {tools.TOOLS[name].__doc__}"
            if state["repeats"]:
                obs = f"[same action as before, repeated {state['repeats']}x - change something] {obs}"
        except (ValueError, OSError, requests.RequestException) as e:
            obs = f"error: {e}"
            show(f"{state['run']:>2}  (bad action)")
        state["observation"] = obs
        state["files"] = tools.snapshot(ctx)
        log(f"[{sid}] observation:\n{obs}")
        if obs != "final_answer recorded":
            show(f"    → {brief(obs, 110)}")

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
