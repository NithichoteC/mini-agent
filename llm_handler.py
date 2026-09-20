"""LLM handler: one provider (Groq, OpenAI-compatible chat endpoint).

Contract:
    call_llm(messages, model, ...) -> {"ok": True,  "text": str, "usage": {...}}
                                   |  {"ok": False, "error": {"code", "message"}}

Never raises. Key comes from .env (GROQ_API_KEY) or the environment.
Docs: https://console.groq.com/docs/api-reference#chat-create
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()  # finds .env next to this file (or in a parent dir); never overrides real env vars

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def call_llm(messages: list[dict], model="qwen/qwen3.8-27b",
             temperature=0.2, max_completion_tokens=2048, timeout_sec=60) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _error("missing_api_key", "GROQ_API_KEY not set (put it in .env)")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,  # "max_tokens" is deprecated on Groq
    }
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
        return _error("http_error", f"{e} :: {r.text[:500]}")
    except requests.RequestException as e:
        return _error("request_failed", str(e))
    except ValueError as e:
        return _error("invalid_json", str(e))

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _error("bad_response", f"unexpected body: {str(body)[:500]}")

    text = message.get("content") or ""
    if not text:
        # reasoning models (gpt-oss on Groq) may put their whole reply in "reasoning" and leave content empty
        where = " (output went to the 'reasoning' field)" if message.get("reasoning") else ""
        return _error("empty_content", f"model {model} returned no content{where}")

    return {"ok": True, "text": text, "usage": body.get("usage", {})}


if __name__ == "__main__":
    # python llm_handler.py "say hi in 3 words"
    prompt = " ".join(sys.argv[1:]) or "say hi in 3 words"
    print(call_llm([{"role": "user", "content": prompt}]))
