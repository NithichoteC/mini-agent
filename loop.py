"""Runs the steps from a workflow YAML, repeating up to loop.max_runs times.

Step types:
    act      the model picks ONE action; the tool runs and its observation goes into state.
             llm.actions selects the protocol: "json_text" (the model writes a ```json block
             that we parse) or "tool_calls" (native function calling: we declare the tools
             and the API returns a structured call; the observation goes back as a tool message)
    llm      free-text model call, reply stored in state[save_as]. With `system:` it runs
             in its own fresh conversation (a separate role, e.g. the reviewer) instead of
             the agent's (`system:` names an entry under prompts: or is literal text);
             `role:` (or `model:`) picks who answers that step, via config/runtime.yaml
    stop_if  end the workflow if its `when` condition holds; result status is
             `status:` from the step (default "done")

Any step may carry `when: <condition>` and is skipped unless it holds.
`state` is one dict shared by all steps; prompt templates may use any key as {key}:
    {task} {hint} {run} {tools} {observation} {answer} {review} {files} {repeats} {actions} {earlier}
{actions} is the engine's own record of what ran (tool, permission decision, real output), so a
reviewer can judge from evidence rather than from the agent's claims.

Callbacks: `log(text)` gets the full transcript, `show(text)` the short console view,
`confirm(tool, args) -> (decision, reason)` is asked whenever permission says "ask"
(see run_workflow's docstring; default: deny).
"""
import json
import re
import time

import requests

import registry
import sandbox
import tools
import tracedb
import web
from llm_handler import call_llm, resolve

PERMITTED = ("allow", "ask_yes", "ask_always")   # decision labels under which the tool actually runs
HISTORY = 12                                      # most recent actions shown to the reviewer
EVIDENCE = 1200                                   # characters of each action's output the reviewer sees
TRIM_KEEP = 2                                     # newest tool outputs always kept whole for the agent
TRIM_ABOVE = 600                                  # characters; a shorter tool output is never trimmed
TRIMMED = "[... older output trimmed"
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
    # accept {"tool": t, "args": {...}}, the flat {"tool": t, "path": ...}, and a mix of both -
    # models write {"tool": "bash", "command": "...", "args": {}}; `args` wins where both name a key
    args = action.get("args", {})
    if not isinstance(args, dict):
        raise ValueError('"args" must be an object')
    flat = {k: v for k, v in action.items() if k not in ("tool", "args")}
    return {"tool": action["tool"], "args": {**flat, **args}}


def brief(value, width=70) -> str:
    """One-line preview of an argument or observation for the console."""
    s = str(value).replace("\n", " ⏎ ")
    return s if len(s) <= width else s[:width - 1] + "…"


def _needs_review(s) -> bool:
    """review: always | auto | never (workflow.yaml). auto reviews a turn that used a tool - there is
    evidence to check - and lets a plain answer stand, the way a chat assistant answers chit-chat."""
    mode = s.get("review_mode", "always")
    return s["answer"] != "" and (mode == "always" or (mode == "auto" and bool(s.get("history"))))


CONDITIONS = {
    "answered": lambda s: s["answer"] != "",
    "needs_review": _needs_review,
    "answered_unreviewed": lambda s: s["answer"] != "" and not _needs_review(s),
    "review_pass": lambda s: s["review"].lstrip().upper().startswith("VERDICT: PASS"),
    "review_blocked": lambda s: s["review"].lstrip().upper().startswith("VERDICT: BLOCKED"),
    "no_progress": lambda s: s["repeats"] >= s["max_repeats"],
}


def _tokens(messages) -> int:
    """A cautious estimate (3 characters a token; Thai and code run denser than English prose)."""
    return sum(len(m.get("content") or "") // 3 + 4 for m in messages)


def trim(messages: list[dict], budget: int | None = None, keep: int = TRIM_KEEP) -> int:
    """Shorten old tool output in the agent's conversation, in place; returns how many were cut.

    Tool output is most of a long conversation and the agent has already acted on it, so all but
    the newest `keep` outputs longer than TRIM_ABOVE keep only their first lines. When the estimate
    is still over `budget` tokens (the provider's per-request limit), the newest are cut as well.
    The trace keeps every output in full; only what is re-sent to the model gets shorter."""
    found = [m for m in messages if isinstance(m.get("content"), str) and len(m["content"]) > TRIM_ABOVE
             and TRIMMED not in m["content"]
             and (m["role"] == "tool" or (m["role"] == "user" and m["content"].startswith("Observation:")))]
    cut = 0
    for n in range(keep, -1, -1):
        for m in found[:len(found) - n] if n else found:
            if TRIMMED in m["content"]:
                continue
            head = "\n".join(m["content"].splitlines()[:2])[:300]
            m["content"] = f"{head}\n{TRIMMED} ({len(m['content']):,} chars) - run the tool again if you need it]"
            cut += 1
        if budget is None or _tokens(messages) <= budget:
            break
    return cut


def who(cfg: dict, step: dict | None = None) -> tuple[str | None, str | None]:
    """(model, role) for a step: the step's own model/role if it names one, else the llm: block's.
    An explicit model beats a role; both resolve through config/runtime.yaml."""
    src = step if step and ("model" in step or "role" in step) else cfg["llm"]
    return src.get("model"), src.get("role")


def model_name(cfg: dict, step: dict | None = None) -> str:
    """The concrete model id a step will use, for headers and the trace."""
    model, role = who(cfg, step)
    t = resolve(model, role, config_path=cfg.get("runtime"))
    return t["model"] if t["ok"] else f"{model or role} (unresolved: {t['error']['code']})"


def run_workflow(cfg: dict, task: str, log=lambda text: None, show=lambda text: None,
                 confirm=lambda tool, args: ("deny", "no human is attached"),
                 previous: dict | None = None, hint: str = "", trace_db=None, emit=lambda event: None,
                 attachments=(), cancel=lambda: False) -> dict:
    """Pass a previous result as `previous` to continue that session - same conversation,
    workspace, token count and trace id:
      with a `hint`: the same turn goes on (the agent was stuck); the task stays the original one
      without one:   a new turn - `task` is the user's next message (main.py chat, run --continue);
                     the reviewer also sees the earlier requests of the conversation ({earlier})

    `confirm(tool, args) -> (decision, reason)` is asked whenever a tool's permission is "ask"
    (its default in the registry, or a yaml rule under `permissions:`). decision is one of:
    "allow" (run this once), "always" (run it, and remember allow for the rest of this session),
    or anything else (declined; `reason`, if given, is sent back to the model as the observation).
    "deny" rules never call this - they are refused without asking.

    `trace_db` is an open tracedb connection (or None): every action, its permission decision
    and every review verdict is written there as it happens. A continued session keeps its id.
    `emit(event)` receives the same rows as dicts ({"type": "step", ...}) - main.py's --format json.
    `attachments` are paths of the user's files for this message: each is copied into the workspace
    and its note (full text, or a pointer to rag_search) is added to the message - tools.attach.
    `cancel()` is checked before every step; when it returns True the turn ends with status
    "stopped" (a model call or command already running finishes first)."""
    llm_cfg, sb_cfg, loop_cfg = cfg["llm"], cfg["sandbox"], cfg["loop"]
    allowed = cfg.get("tools", [])
    reg = registry.load(cfg.get("registry", registry.DEFAULT_PATH))
    registry.check(reg, allowed)
    web.check_config(cfg.get("web") or {})
    native = llm_cfg.get("actions", "json_text") == "tool_calls"
    schemas = registry.schemas(reg, allowed) if native else None

    if previous:
        workspace, messages, total_tokens = previous["workspace"], previous["messages"], previous["total_tokens"]
        state = {**previous["state"], "hint": hint, "answer": "", "review": "", "repeats": 0}
        if not hint:                                   # a new user message: a new turn
            asked = state.get("requests") or [state["task"]]      # not `requests`: that is the http module
            state.update(task=task, requests=asked + [task], history=[], actions="(none yet)",
                         earlier="\n".join(f"{i}. {r}" for i, r in enumerate(asked, 1)))
    else:
        workspace = sandbox.new_workspace(sb_cfg["dir"])
        messages, total_tokens = [], 0
        state = {"task": task, "hint": "", "run": 0, "tools": registry.describe(reg, allowed),
                 "observation": "", "answer": "", "review": "", "files": "",
                 "repeats": 0, "max_repeats": loop_cfg.get("max_repeats", 3), "last_action": None,
                 "history": [], "actions": "(none yet)", "requests": [task], "earlier": "(none)"}
        messages.append({"role": "system", "content": render(cfg["prompts"]["system"], state)})
    state["review_mode"] = cfg.get("review", "always")
    model, role = who(cfg)
    budget = (resolve(model, role, config_path=cfg.get("runtime")).get("limits") or {}).get("request_tokens")
    ctx = {"workspace": workspace, "sandbox": sb_cfg, "web": cfg.get("web") or {}, "rag": cfg.get("rag") or {}}
    for path in attachments:
        note = tools.attach(ctx, path)
        show(f"  \u25c7 {note.splitlines()[0]}")
        state["task"] += "\n\n" + note
    # "always" answers live in the session (like opencode's approved list), so they survive a hint
    # and every later turn of a chat - not just the one run_workflow call they were given in
    rules = registry.rules(reg, allowed, cfg.get("permissions", [])) + state.setdefault("approved", [])
    trace_id = previous.get("trace_id") if previous else None
    if trace_db is not None and trace_id is None:
        trace_id = tracedb.start_session(trace_db, workspace, workflow=cfg.get("name"),
                                         model=model_name(cfg), task=state["task"])
    last_tokens, last_model = 0, None
    if not previous:
        emit({"type": "session.start", "session": trace_id, "task": state["task"],
              "model": model_name(cfg), "workspace": str(workspace)})

    def record(**fields):
        row = {"run": state["run"], "tokens": last_tokens, "model": last_model, **fields}
        if trace_db is not None:
            tracedb.step(trace_db, trace_id, **row)
        emit({"type": "step", "session": trace_id, **row,
              "observation": sandbox.truncate(row.get("observation") or "", sb_cfg["max_output_chars"])})

    # what the human said goes into the trace too, so a session reads as a conversation
    record(step_id="hint" if hint else "user", run=0, tokens=0, model=None, observation=hint or state["task"])

    def ask(step, sid):
        nonlocal total_tokens, last_tokens, last_model
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
            cut = trim(messages, int(budget * 0.85) if budget else None)
            if cut:
                log(f"[{sid}] trimmed {cut} old tool output(s); ~{_tokens(messages):,} tokens now")
        model, role = who(cfg, step)
        res = call_llm(convo, model=model, role=role, temperature=llm_cfg.get("temperature"),
                       max_completion_tokens=llm_cfg.get("max_completion_tokens", 2048),
                       tools=schemas if use_tools else None, config_path=cfg.get("runtime"))
        last_model = res.get("model") or (res.get("error") or {}).get("model")
        if res["ok"]:
            if convo is messages:
                messages.append(res["message"] if use_tools else {"role": "assistant", "content": res["text"]})
            last_tokens = res["usage"].get("total_tokens", 0)
            total_tokens += last_tokens
            log(f"[{sid}] tokens so far={total_tokens}\n{res['text'] or json.dumps(res.get('tool_calls', []))}")
        else:
            show(f"    llm error: {res['error']['message']}")
        return res

    def result(status, **extra):
        if trace_db is not None:
            tracedb.finish(trace_db, trace_id, status=status, runs=extra.get("runs", state["run"]),
                           total_tokens=total_tokens, answer=state["answer"])
        return {"status": status, "total_tokens": total_tokens, "state": state,
                "messages": messages, "workspace": workspace, "trace_id": trace_id, **extra}

    def permitted(name, call_args):
        """(decision, message): decision is the label that goes into the trace, one of
        allow / deny / ask_yes / ask_always / ask_no; the tool runs only for those in PERMITTED.
        message is the observation to send back when it does not."""
        decision = registry.decide_call(rules, reg, name, call_args)
        if decision == "deny":
            return "deny", f"{name} is blocked by policy for {registry._pattern(reg, name, call_args)!r}"
        if decision == "allow":
            return "allow", ""
        reply, reason = confirm(name, call_args)             # decision == "ask"
        if reply == "always":
            granted = [{"tool": name, "pattern": p, "action": "allow"} for p in registry.always_scope(reg, name, call_args)]
            rules.extend(granted)
            state["approved"].extend(granted)
            return "ask_always", ""
        if reply == "allow":
            return "ask_yes", ""
        return "ask_no", f"the user declined to run {name}" + (f": {reason}" if reason else "")

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
        name, args, decision, ok, t0 = None, None, "invalid", False, time.time()
        try:
            action = take_action()
            name, args = action["tool"], action["args"]
            icon, title = ("\u25b8", "Final answer") if name == "final_answer" else registry.render(reg, name, args)
            show(f"{state['run']:>2} {icon} {brief(title, 100)}")
            fingerprint = json.dumps(action, sort_keys=True)
            state["repeats"] = state["repeats"] + 1 if fingerprint == state["last_action"] else 0
            state["last_action"] = fingerprint
            if name == "final_answer":
                state["answer"] = str(args.get("answer", "")).strip() or "(empty answer)"
                obs, decision, ok = "final_answer recorded", "final_answer", True
            elif name not in allowed:
                obs = f"unknown tool '{name}'; allowed: {', '.join(allowed)}, final_answer"
            else:
                call_args = registry.validate(reg, name, args)   # bad arguments become an observation
                decision, message = permitted(name, call_args)
                if decision not in PERMITTED:
                    obs = message
                else:
                    try:
                        obs, ok = tools.TOOLS[name](ctx, **call_args), True
                    except TypeError as e:   # should be unreachable once validate() has run
                        obs = f"error: {e}. usage: {registry.signature(reg, name)}"
            if state["repeats"]:
                obs = f"[same action as before, repeated {state['repeats']}x - change something] {obs}"
        except (ValueError, OSError, requests.RequestException) as e:
            obs, ok = f"error: {e}", False
            show(f"{state['run']:>2}  (bad action)")
        state["observation"] = obs
        state["files"] = tools.snapshot(ctx)
        for i, call in enumerate(calls):   # native protocol: every tool_call needs a tool message back
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": obs if i == 0 else "skipped: one action per turn, the first one ran"})
        log(f"[{sid}] observation:\n{obs}")
        if name != "final_answer":
            label = registry.render(reg, name, args)[1] if name in reg["tools"] and isinstance(args, dict) \
                else f"{name or 'unparsed reply'}"
            seen = " ".join(str(obs).split())
            if len(seen) > EVIDENCE:
                seen = f"{seen[:EVIDENCE]} [... {len(seen) - EVIDENCE} more chars the agent saw but you do not]"
            state.setdefault("history", []).append(f"{state['run']}. {label} [{decision}] -> {seen}")
            state["actions"] = "\n".join(state["history"][-HISTORY:])
        record(step_id=sid, tool=name or "(unparsed)", args=args, decision=decision, ok=ok,
               model_output=state["llm_text"] or json.dumps(state["tool_calls"]), observation=obs,
               duration_ms=int((time.time() - t0) * 1000))
        if obs != "final_answer recorded":
            show(f"    → {brief(obs, 110)}")

    for run_no in range(1, loop_cfg["max_runs"] + 1):
        state["run"] = run_no
        log(f"\n===== run {run_no}/{loop_cfg['max_runs']} =====")

        for step in cfg["steps"]:
            if cancel():
                show("    stopped by the user")
                return result("stopped", runs=run_no)
            kind, sid = step["type"], step.get("id", step["type"])
            if kind != "stop_if" and "when" in step and not CONDITIONS[step["when"]](state):
                continue

            if kind in ("act", "llm"):
                res = ask(step, sid)
                if not res["ok"]:
                    record(step_id=sid, ok=False, observation=res["error"]["message"])
                    return result("llm_error", error=res["error"])
                state["llm_text"], state["tool_calls"] = res["text"], res.get("tool_calls", [])
                if kind == "act":
                    act(step, sid)
                else:
                    state[step.get("save_as", "llm_text")] = res["text"]
                    record(step_id=sid, model_output=res["text"])
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
