"""Runs the steps from a workflow YAML, repeating up to loop.max_runs times.

Step types:
    act      the model picks ONE action; the tool runs and its observation goes into state.
             llm.actions selects the protocol: "json_text" (the model writes a ```json block
             that we parse) or "tool_calls" (native function calling: we declare the tools
             and the API returns a structured call; the observation goes back as a tool message)
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


def _json_objects(text: str):
    """Every JSON object that starts with a "tool" key, wherever it sits in the text."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r'\{\s*"tool"', text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
            yield obj
        except json.JSONDecodeError:
            continue


def parse_action(text: str) -> dict:
    """Last ```json block if there is one, else the last {"tool": ...} object in the text
    (models drop the fence, or wrap the action in prose). Raises ValueError with a message
    meant to go back to the model as the observation."""
    blocks = JSON_FENCE.findall(text)
    if blocks:
        try:
            action = json.loads(blocks[-1])
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid json: {e}")
    else:
        found = list(_json_objects(text))
        if not found:
            raise ValueError('no json action found; reply with ```json {"tool": ..., "args": {...}} ```')
        action = found[-1]
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
    native = llm_cfg.get("actions", "json_text") == "tool_calls"
    schemas = tools.schemas(allowed) if native else None

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
        use_tools = native and step["type"] == "act"
        template = step["prompt"]
        if state["run"] == 1 and state["hint"] and "hint_prompt" in step:
            template = step["hint_prompt"]
        elif state["run"] > 1 and "retry_prompt" in step:
            # with native tool calls the observation already went back as a tool message,
            # so the follow-up only carries the review (if any)
            template = step.get("review_prompt", "{review}") if use_tools else step["retry_prompt"]
        content = render(template, state).strip()
        user = {"role": "user", "content": content}
        if "system" in step:   # separate conversation for this role; names a prompts: entry or is literal text
            system = cfg["prompts"].get(step["system"], step["system"])
            convo = [{"role": "system", "content": render(system, state)}, user]
        else:
            if content:
                messages.append(user)
            convo = messages
        res = call_llm(convo, model=step.get("model", llm_cfg["model"]), temperature=llm_cfg["temperature"],
                       max_completion_tokens=llm_cfg["max_completion_tokens"],
                       tools=schemas if use_tools else None)
        if res["ok"]:
            if convo is messages:
                messages.append(res["message"] if use_tools else {"role": "assistant", "content": res["text"]})
            total_tokens += res["usage"].get("total_tokens", 0)
            log(f"[{sid}] tokens so far={total_tokens}\n{res['text'] or json.dumps(res.get('tool_calls', []))}")
        else:
            show(f"    llm error: {res['error']['message']}")
        return res

    def result(status, **extra):
        return {"status": status, "total_tokens": total_tokens, "state": state,
                "messages": messages, "workspace": workspace, **extra}

    def take_action():
        """The one action for this turn, from either protocol."""
        if not native:
            return parse_action(state["llm_text"])
        calls = state["tool_calls"]
        if not calls:   # a plain text reply is how a tool-calling model says it is done
            return {"tool": "final_answer", "args": {"answer": state["llm_text"]}}
        fn = calls[0]["function"]
        try:
            args = json.loads(fn["arguments"] or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"tool arguments are not valid json: {e}")
        return {"tool": fn["name"], "args": args}

    def act(step, sid):
        state["answer"], state["review"] = "", ""
        calls = state["tool_calls"] if native else []
        try:
            action = take_action()
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
        for i, call in enumerate(calls):   # native protocol: every tool_call needs a tool message back
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": obs if i == 0 else "skipped: one action per turn, the first one ran"})
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
                state["llm_text"], state["tool_calls"] = res["text"], res.get("tool_calls", [])
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
