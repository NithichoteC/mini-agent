"""LLM handler: a small config-first router over OpenAI-compatible chat endpoints. Never raises.

Which model and which endpoint live in config/runtime.yaml, resolved the way the course's own
handler does it (class-day3):

    role -> model_ref -> model -> vendor -> endpoint_profile

Entry points:

    call_llm(messages, role=..., model=..., tools=None, ...)   multi-turn; what the agent loop uses
        -> {"ok": True, "text": str, "tool_calls": [...], "message": {...}, "usage": {...}, "model": str}
         | {"ok": False, "error": {"code", "message", "provider", "model"}}

    call_LLM(model=None, prompt="", role=None, provider=None)  single prompt, the course's signature
        -> str on success, or the same error dict as above.  `model` may be a key under models:
           ("qwen_27b"), like the course's call_LLM("dji", ...), or a raw model id.

    resolve(model=None, role=None, vendor=None) -> the concrete target, or the error dict

Keys come from .env or the environment; a vendor in the yaml names the variable, never the key,
and any key is scrubbed out of error text before it is returned (sanitize).
"""
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()  # finds .env next to this file (or in a parent dir); never overrides real env vars

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = "config/runtime.yaml"
PROFILES = ("openai_chat_compatible",)   # the only request/response shape implemented


def _error(code: str, message: str, model, provider=None) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "provider": provider, "model": model}}


def sanitize(message: str, secret: str | None) -> str:
    """The key must never come back inside an error, a log line or the trace."""
    return message.replace(secret, "***") if secret else message


def load_runtime(path: str | None = None) -> dict:
    p = Path(path or os.getenv("LLM_RUNTIME_CONFIG") or DEFAULT_CONFIG)
    return yaml.safe_load((p if p.is_absolute() else ROOT / p).read_text()) or {}


def resolve(model: str | None = None, role: str | None = None, vendor: str | None = None,
            config_path: str | None = None) -> dict:
    """An explicit model wins over a role: first as a key under models:, then as a raw model id
    sent to the vendor the role (or `vendor`) would have used. With neither, runtime.default_role."""
    try:
        rt = load_runtime(config_path)
    except (OSError, yaml.YAMLError) as e:
        return _error("bad_runtime_config", f"cannot read runtime config: {e}", model or role)
    vendors, models, roles = rt.get("vendors") or {}, rt.get("models") or {}, rt.get("roles") or {}
    settings = rt.get("runtime") or {}

    role_name = role or settings.get("default_role")
    role_cfg = roles.get(role_name) if role_name else None
    if role and role_cfg is None:
        return _error("unknown_role", f"role '{role}' is not in runtime.yaml (have: {', '.join(roles)})", role)

    if model and model in models:
        target = models[model]
    elif model:
        base = models.get(role_cfg["model_ref"], {}) if role_cfg else {}
        target = {"vendor": vendor or base.get("vendor") or next(iter(vendors), None), "model": model}
    elif role_cfg:
        target = models.get(role_cfg.get("model_ref"))
        if target is None:
            return _error("unknown_model_ref", f"role '{role_name}' points to missing model_ref "
                                               f"'{role_cfg.get('model_ref')}'", role_name)
    else:
        return _error("no_model", "no model, no role, and no runtime.default_role", None)

    v = vendors.get(target["vendor"])
    if v is None:
        return _error("unknown_vendor", f"vendor '{target['vendor']}' is not in runtime.yaml", target["model"])
    profile = v.get("endpoint_profile", PROFILES[0])
    if profile not in PROFILES:
        return _error("unsupported_profile", f"endpoint_profile '{profile}' is not implemented "
                                             f"(have: {', '.join(PROFILES)})", target["model"], target["vendor"])
    return {"ok": True, "vendor": target["vendor"], "model": target["model"], "endpoint": v["endpoint"],
            "key_env": v.get("key_env"), "requires_api_key": v.get("requires_api_key", True),
            "timeout": v.get("request_timeout", settings.get("request_timeout", 60)),
            "options": {**(v.get("options") or {}), **(target.get("options") or {}),
                        **((role_cfg or {}).get("options") or {})}}


RETRIES = 5        # on top of the first request
MAX_WAIT = 30      # seconds per retry; Groq's limits are per minute, so a few waits can clear one


def _tool_use_failed(r) -> bool:
    """Groq's tool_use_failed: the model's output could not be read as a tool call. Seen both ways:
    gpt-oss calling a tool it was never given, and qwen writing a malformed call in native mode.
    It is a decode fluke, not a bad payload, so it is retried like a rate limit (bounded)."""
    if r.status_code != 400:
        return False
    try:
        return r.json().get("error", {}).get("code") == "tool_use_failed"
    except ValueError:
        return False


def _retry_after(r, attempt: int) -> float:
    """The longer of the server's hint (header, or "try again in 7.2s" in the body) and a backoff."""
    hints = [r.headers.get("retry-after") or 0]
    m = re.search(r"try again in ([\d.]+)(ms|s)", getattr(r, "text", "") or "")
    if m:
        hints.append(float(m.group(1)) / (1000 if m.group(2) == "ms" else 1))
    try:
        hint = max(float(h) for h in hints)
    except ValueError:
        hint = 0
    return min(max(hint, 2 ** (attempt + 1)), MAX_WAIT)


def call_llm(messages: list[dict], model: str | None = None, role: str | None = None,
             temperature: float | None = None, max_completion_tokens: int = 2048,
             tools: list[dict] | None = None, tool_choice: str = "auto",
             config_path: str | None = None) -> dict:
    """`temperature` is the caller's default; options from runtime.yaml (vendor, model, role) win."""
    t = resolve(model, role, config_path=config_path)
    if not t["ok"]:
        return t
    model_id, vendor = t["model"], t["vendor"]
    key = os.getenv(t["key_env"]) if t["key_env"] else None
    if t["requires_api_key"] and not key:
        return _error("missing_api_key", f"{t['key_env']} not set (put it in .env)", model_id, vendor)

    payload = {"model": model_id, "messages": messages,
               "max_completion_tokens": max_completion_tokens}  # "max_tokens" is deprecated on Groq
    if temperature is not None:
        payload["temperature"] = temperature
    payload.update(t["options"])
    if tools:   # native tool calling: the model answers with structured tool_calls instead of text
        payload["tools"], payload["tool_choice"] = tools, tool_choice
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    r = None
    try:
        r = requests.post(t["endpoint"], headers=headers, json=payload, timeout=t["timeout"])
        for attempt in range(RETRIES):
            if r.status_code == 429:
                wait = _retry_after(r, attempt)
                print(f"    … rate limited, retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
            elif _tool_use_failed(r):
                print(f"    … {model_id} produced a tool call the API could not read, retrying", file=sys.stderr)
            else:
                break
            r = requests.post(t["endpoint"], headers=headers, json=payload, timeout=t["timeout"])
        r.raise_for_status()
        body = r.json()
    except requests.HTTPError as e:
        detail = r.text[:500] if r is not None else ""
        return _error("http_error", sanitize(f"{e} :: {detail}", key), model_id, vendor)
    except requests.RequestException as e:
        return _error("request_failed", sanitize(str(e), key), model_id, vendor)
    except ValueError as e:
        return _error("invalid_json", sanitize(str(e), key), model_id, vendor)

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _error("bad_response", sanitize(f"unexpected body: {str(body)[:500]}", key), model_id, vendor)

    text = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    if not text and not tool_calls:
        # reasoning models (gpt-oss on Groq) may leave content empty and put the reply in "reasoning"
        where = " (output went to the 'reasoning' field)" if message.get("reasoning") else ""
        return _error("empty_content", f"model {model_id} returned no content{where}", model_id, vendor)

    return {"ok": True, "text": text, "tool_calls": tool_calls, "usage": body.get("usage", {}), "model": model_id,
            "message": {k: message[k] for k in ("role", "content", "tool_calls") if k in message}}


def call_LLM(model: str | None = None, prompt: str = "", role: str | None = None,
             provider: str | None = None) -> str | dict:
    """Single-prompt call with the course's signature. Resolution: model, else role, else default role."""
    if provider is not None:
        try:
            vendors = load_runtime().get("vendors") or {}
        except (OSError, yaml.YAMLError) as e:
            return _error("bad_runtime_config", f"cannot read runtime config: {e}", model)
        if provider not in vendors:
            return _error("unsupported_provider",
                          f"provider '{provider}' is not in runtime.yaml (have: {', '.join(vendors)})", model, provider)
    t = resolve(model, role, vendor=provider)
    if not t["ok"]:
        return t
    res = call_llm([{"role": "user", "content": prompt}], model=model, role=role)
    return res["text"] if res["ok"] else res


if __name__ == "__main__":
    # python llm_handler.py "say hi in 3 words"
    print(call_LLM(prompt=" ".join(sys.argv[1:]) or "say hi in 3 words"))
