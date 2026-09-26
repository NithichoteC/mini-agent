"""The tool registry. config/tools.json is the source of truth for what the model is told
and for what a tool is allowed to receive; tools.py only holds the implementations.

    reg = load()                        read and self-check the json
    audit(reg)                          list every place the json and tools.py disagree
    describe(reg, enabled) -> str       the tool list for the prompt (json_text protocol)
    schemas(reg, enabled) -> [dict]     OpenAI function schemas (tool_calls protocol)
    validate(reg, name, args) -> dict   type-coerced arguments, or ValueError for the model
    render(reg, name, args) -> (icon, title)
    rules(reg, enabled, overrides) -> [{tool, pattern, action}]   registry defaults + yaml overrides
    decide(rules, tool, pattern) -> "allow"|"ask"|"deny"          the last rule matching both levels
    decide_call(rules, reg, tool, args) -> action   decide() on the call's pattern argument; for a
                                                     shell command, on every command in the chain
    always_scope(reg, tool, args) -> [patterns]      what an "always" answer allows from now on

validate() raises ValueError with a message written for the model, not for a developer:
its text has to be enough for the next turn to get the call right.
"""
import fnmatch
import inspect
import json
import re
from pathlib import Path

import tools

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = "config/tools.json"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")   # opencode's rule
ACTIONS = ("allow", "ask", "deny")
TYPES = ("string", "integer", "number", "boolean")
TRUE, FALSE = ("true", "yes", "1", "on"), ("false", "no", "0", "off")
SPLITS = (None, "shell")
SHELL_SPLIT = re.compile(r"&&|\|\||\$\(|[;|&\n()`]")   # the places one shell command can start another
STRICTNESS = {"allow": 0, "ask": 1, "deny": 2}


def load(path: str = DEFAULT_PATH) -> dict:
    p = Path(path)
    reg = json.loads((p if p.is_absolute() else ROOT / p).read_text())
    for name, spec in reg["tools"].items():
        if not NAME_RE.match(name):
            raise ValueError(f"tool name '{name}' is not allowed by {NAME_RE.pattern}")
        if spec.get("permission", "ask") not in ACTIONS:
            raise ValueError(f"{name}: permission must be one of {ACTIONS}")
        if not spec.get("description"):
            raise ValueError(f"{name}: a description is required, the model sees it")
        for arg, meta in spec["args"].items():
            if meta.get("type") not in TYPES:
                raise ValueError(f"{name}.{arg}: type must be one of {TYPES}")
            if "enum" in meta and not (isinstance(meta["enum"], list) and meta["enum"]):
                raise ValueError(f"{name}.{arg}: enum must be a non-empty list")
        if spec.get("pattern_arg") and spec["pattern_arg"] not in spec["args"]:
            raise ValueError(f"{name}: pattern_arg '{spec['pattern_arg']}' is not one of its args")
        if spec.get("pattern_split") not in SPLITS:
            raise ValueError(f"{name}: pattern_split must be one of {SPLITS}")
    return reg


def audit(reg: dict) -> list[str]:
    """Every disagreement between the registry and tools.py. The test asserts this is empty."""
    problems = []
    for name in set(reg["tools"]) | set(tools.TOOLS):
        if name not in tools.TOOLS:
            problems.append(f"{name}: in the registry but not in tools.TOOLS")
            continue
        if name not in reg["tools"]:
            problems.append(f"{name}: in tools.TOOLS but not in the registry")
            continue
        declared = reg["tools"][name]["args"]
        params = {k: v for k, v in inspect.signature(tools.TOOLS[name]).parameters.items() if k != "ctx"}
        for arg in declared.keys() - params.keys():
            problems.append(f"{name}.{arg}: declared in the registry, not in the signature")
        for arg in params.keys() - declared.keys():
            problems.append(f"{name}.{arg}: in the signature, not declared in the registry")
        for arg in declared.keys() & params.keys():
            required = bool(declared[arg].get("required"))
            has_default = params[arg].default is not inspect.Parameter.empty
            if required == has_default:
                problems.append(f"{name}.{arg}: required={required} but the signature "
                                f"{'has' if has_default else 'has no'} default")
    return sorted(problems)


def check(reg: dict, enabled: list[str]) -> None:
    """Every enabled name must exist in both places. Raises before the agent starts."""
    unknown = [n for n in enabled if n not in reg["tools"]]
    if unknown:
        raise ValueError(f"config enables tools that are not in the registry: {', '.join(unknown)}")
    missing = [n for n in enabled if n not in tools.TOOLS]
    if missing:
        raise ValueError(f"registry names tools that tools.py does not implement: {', '.join(missing)}")


def signature(reg: dict, name: str) -> str:
    return f"{name}({', '.join(reg['tools'][name]['args'])})"


def describe(reg: dict, enabled: list[str]) -> str:
    """One line per allowed tool, for the system prompt."""
    lines = []
    for name in enabled:
        spec = reg["tools"][name]
        lines.append(f"{signature(reg, name)} - {spec['description']}")
        for arg, meta in spec["args"].items():
            opt = "" if meta.get("required") else " (optional)"
            choices = f" (one of: {', '.join(map(str, meta['enum']))})" if "enum" in meta else ""
            lines.append(f"    {arg}: {meta['description']}{choices}{opt}")
    return "\n".join(lines + [tools.FINAL_ANSWER_DOC])


def schemas(reg: dict, enabled: list[str]) -> list[dict]:
    """The same tools as OpenAI-style function schemas, for native tool calling."""
    out = []
    for name in enabled:
        spec = reg["tools"][name]
        out.append({"type": "function", "function": {
            "name": name,
            "description": spec["description"],
            "parameters": {
                "type": "object",
                "properties": {a: {"type": m["type"], "description": m["description"],
                                   **({"enum": m["enum"]} if "enum" in m else {})}
                               for a, m in spec["args"].items()},
                "required": [a for a, m in spec["args"].items() if m.get("required")]}}})
    out.append({"type": "function", "function": {
        "name": "final_answer",
        "description": tools.FINAL_ANSWER_DOC.split(" - ", 1)[-1],
        "parameters": {"type": "object",
                       "properties": {"answer": {"type": "string", "description": "the reply to the user"}},
                       "required": ["answer"]}}})
    return out


def _coerce(name: str, arg: str, kind: str, value):
    if kind == "string":
        return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        low = str(value).strip().lower()
        if low in TRUE:
            return True
        if low in FALSE:
            return False
        raise ValueError(f"{name}: {arg} must be true or false, got {value!r}")
    if isinstance(value, bool):
        raise ValueError(f"{name}: {arg} must be a{'n' if kind == 'integer' else ''} {kind}, got {value!r}")
    try:
        return int(str(value).strip()) if kind == "integer" else float(str(value).strip())
    except ValueError:
        raise ValueError(f"{name}: {arg} must be a{'n' if kind == 'integer' else ''} {kind}, got {value!r}")


def validate(reg: dict, name: str, args: dict) -> dict:
    """Arguments the tool can actually be called with, or a ValueError the model can act on."""
    declared = reg["tools"][name]["args"]
    for arg in args:
        if arg not in declared:
            raise ValueError(f"unknown argument '{arg}' for {name}; usage: {signature(reg, name)}")
    out = {}
    for arg, meta in declared.items():
        if arg not in args or args[arg] is None:
            if meta.get("required"):
                raise ValueError(f"{name} is missing required argument '{arg}'; usage: {signature(reg, name)}")
            continue
        out[arg] = _coerce(name, arg, meta["type"], args[arg])
        if "enum" in meta and out[arg] not in meta["enum"]:
            raise ValueError(f"{name}: {arg} must be one of {', '.join(map(str, meta['enum']))}, got {args[arg]!r}")
    return out


def rules(reg: dict, enabled: list[str], overrides: list[dict] | None = None) -> list[dict]:
    """[the registry's own default for each enabled tool] + [yaml overrides], in that order.
    A later rule wins when both match, so an override always beats the tool's default."""
    defaults = [{"tool": name, "pattern": "*", "action": reg["tools"][name]["permission"]} for name in enabled]
    overrides = list(overrides or [])
    for rule in overrides:
        if rule.get("action") not in ACTIONS:
            raise ValueError(f"permissions: invalid action {rule.get('action')!r} for tool {rule.get('tool')!r}")
    return defaults + overrides


def decide(rule_list: list[dict], tool: str, pattern: str) -> str:
    """The action of the LAST rule matching both the tool name and the pattern glob;
    'ask' if nothing matches at all. Pure - no I/O, easy to test on its own."""
    match = None
    for rule in rule_list:
        if fnmatch.fnmatch(tool, rule["tool"]) and fnmatch.fnmatch(pattern, rule.get("pattern", "*")):
            match = rule
    return match["action"] if match else "ask"


def segments(command: str) -> list[str]:
    """`touch a && rm a | tee x; $(curl y)` -> ["touch a", "rm a", "tee x", "curl y"]. Best effort:
    `sh -c "..."`, eval and friends still hide a command inside an argument."""
    return [part.strip() for part in SHELL_SPLIT.split(command) if part.strip()]


def _pattern(reg: dict, tool: str, args: dict) -> str:
    arg = reg["tools"][tool].get("pattern_arg")
    return str(args.get(arg, "")) if arg else "*"


def decide_call(rule_list: list[dict], reg: dict, tool: str, args: dict) -> str:
    """decide() for one call. A shell command is judged as a whole AND as each command in its chain,
    and the strictest answer wins - so `touch a && rm a` meets an `rm *` deny rule, and
    `python3 x.py; curl y` cannot ride on a `python3 *` allow rule. Found live: the model chained
    commands on its own and walked straight past a deny rule matched against the whole string."""
    pattern = _pattern(reg, tool, args)
    parts = [pattern]
    if reg["tools"][tool].get("pattern_split") == "shell":
        parts += segments(pattern)
    return max((decide(rule_list, tool, p) for p in parts), key=STRICTNESS.get)


def always_scope(reg: dict, tool: str, args: dict) -> list[str]:
    """What answering "always" allows for the rest of the session. For a shell command: the first word
    of each command in the chain (opencode keeps a command prefix the same way, permission/arity.ts)
    - approving `ls` once must not approve every future shell command. Other tools: everything."""
    if reg["tools"][tool].get("pattern_split") != "shell":
        return ["*"]
    words = sorted({seg.split()[0] for seg in segments(_pattern(reg, tool, args)) if seg.split()})
    return [p for w in words for p in (w, f"{w} *")]


def render(reg: dict, name: str, args: dict) -> tuple[str, str]:
    """The console line for one call: a one-glyph icon and a human title."""
    spec = reg["tools"].get(name)
    if not spec:
        return "✗", f"Unknown tool {name}"
    title = re.sub(r"\{(\w+)\}", lambda m: str(args.get(m.group(1), "")).strip(), spec.get("title", name))
    return spec.get("icon", "⚙"), " ".join(title.split())
