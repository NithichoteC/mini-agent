"""LLM handler for Groq (OpenAI-compatible chat endpoint). Never raises.

Two entry points:

    call_llm(messages, model=..., tools=None, ...)   multi-turn; what the agent loop uses
        -> {"ok": True, "text": str, "tool_calls": [...], "message": {...}, "usage": {...}}
         | {"ok": False, "error": {"code", "message", "provider", "model"}}

    call_LLM(model=None, prompt="", role=None, provider=None)    single prompt, course-style
        -> str on success, or the same error dict as above

Key comes from .env (GROQ_API_KEY) or the environment.
Docs: https://console.groq.com/docs/api-reference#chat-create
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()  # finds .env next to this file (or in a parent dir); never overrides real env vars

PROVIDER = "groq"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.8-27b"

# role -> model, so callers can say role="reviewer" instead of naming a model
ROLE_DEFAULTS = {
    "actor": DEFAULT_MODEL,
    "reviewer": DEFAULT_MODEL,
}


def _error(code: str, message: str, model: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "provider": PROVIDER, "model": model}}


def call_llm(messages: list[dict], model: str = DEFAULT_MODEL, temperature: float = 0.2,
             max_completion_tokens: int = 2048, timeout_sec: int = 60,
             tools: list[dict] | None = None, tool_choice: str = "auto") -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _error("missing_api_key", "GROQ_API_KEY not set (put it in .env)", model)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,  # "max_tokens" is deprecated on Groq
    }
    if tools:   # native tool calling: the model answers with structured tool_calls instead of text
        payload["tools"], payload["tool_choice"] = tools, tool_choice
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        for attempt in range(4):
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=timeout_sec)
            if r.status_code != 429:
                break
            wait = float(r.headers.get("retry-after", 2 ** attempt))
            print(f"    … rate limited, retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        r.raise_for_status()
        body = r.json()
    except requests.HTTPError as e:
        return _error("http_error", f"{e} :: {r.text[:500]}", model)
    except requests.RequestException as e:
        return _error("request_failed", str(e), model)
    except ValueError as e:
        return _error("invalid_json", str(e), model)

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _error("bad_response", f"unexpected body: {str(body)[:500]}", model)

    text = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    if not text and not tool_calls:
        # reasoning models (gpt-oss on Groq) may leave content empty and put the reply in "reasoning"
        where = " (output went to the 'reasoning' field)" if message.get("reasoning") else ""
        return _error("empty_content", f"model {model} returned no content{where}", model)

    return {"ok": True, "text": text, "tool_calls": tool_calls, "usage": body.get("usage", {}),
            "message": {k: message[k] for k in ("role", "content", "tool_calls") if k in message}}


def call_LLM(model: str | None = None, prompt: str = "", role: str | None = None,
             provider: str | None = None) -> str | dict:
    """Single-prompt call with the course's signature. Resolution: explicit model, else role, else default."""
    chosen = model or ROLE_DEFAULTS.get(role or "", DEFAULT_MODEL)
    if provider not in (None, PROVIDER):
        return _error("unsupported_provider", f"only '{PROVIDER}' is supported, got '{provider}'", chosen)
    res = call_llm([{"role": "user", "content": prompt}], model=chosen)
    return res["text"] if res["ok"] else res


if __name__ == "__main__":
    # python llm_handler.py "say hi in 3 words"
    print(call_LLM(prompt=" ".join(sys.argv[1:]) or "say hi in 3 words"))
