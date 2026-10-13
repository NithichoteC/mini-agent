"""Extract semantic paths from text using an LLM.

This is the only file in `graph_library` allowed to call an LLM. It
does not talk to any provider directly, does not manage API keys, and does
not do retrieval or graph building. Its one job: text -> list of validated
semantic path dicts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .schema import validate_paths

DEFAULT_SCHEMA_HINT = """Each path must be a JSON object with exactly these fields:
- "source": string, the originating entity/event/condition
- "relation": string, a short verb phrase describing the relationship (e.g. "delayed", "triggered", "constrained", "resolved", "resumed_after")
- "target": string, the resulting entity/event/condition
- "evidence": string, the exact sentence(s) from the text that support this path
- "confidence": number between 0 and 1, how certain the relationship is stated in the text"""


def build_extraction_prompt(text: str, schema: dict | None = None) -> str:
    """Build the LLM prompt used to extract semantic paths from `text`.

    `schema` is an optional dict that can override the default field
    description (key "schema_hint") and/or supply a list of suggested
    relation names (key "relations") to bias extraction. Phase 1 keeps this
    simple and does not validate `schema` beyond reading these two keys.
    """
    schema_hint = DEFAULT_SCHEMA_HINT
    relations_hint = ""
    if isinstance(schema, dict):
        if isinstance(schema.get("schema_hint"), str) and schema["schema_hint"].strip():
            schema_hint = schema["schema_hint"]
        relations = schema.get("relations")
        if isinstance(relations, (list, tuple)) and relations:
            relations_hint = (
                "\nPrefer (but are not limited to) these relation names when they fit: "
                + ", ".join(str(r) for r in relations)
            )

    return f"""Extract the semantic relationships (paths) expressed in the text below.

A path captures one relationship between two entities, events, or conditions,
such as a causal link, a dependency, a constraint, or a temporal sequence.
Do not extract isolated facts that have no relationship to another entity.

{schema_hint}{relations_hint}

Return ONLY a JSON array of path objects. No prose, no markdown code fences,
no explanation -- just the JSON array. If the text contains no extractable
relationship, return an empty JSON array: []

Text:
\"\"\"{text}\"\"\"
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text.strip())
    return text.strip()


def _strip_think_blocks(text: str) -> str:
    """Strip <think>...</think> blocks that some models emit before the JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _parse_json_paths(raw_output: str) -> list[Any]:
    """Parse the LLM's raw text output into a list of raw path dicts.

    Raises ValueError if no JSON array/object can be located -- extraction
    failures must be visible, not swallowed into an empty result.
    """
    text = _strip_code_fence(raw_output)
    text = _strip_think_blocks(text)

    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1 and end > start:
            snippet = text[start : end + 1]
            try:
                parsed = json.loads(snippet)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Could not parse a JSON array of paths from LLM output. "
                    f"Raw output (truncated): {raw_output[:500]!r}"
                ) from exc
        else:
            raise ValueError(
                "LLM output did not contain a JSON array. "
                f"Raw output (truncated): {raw_output[:500]!r}"
            )

    if isinstance(parsed, dict):
        parsed = parsed.get("paths", [])

    if not isinstance(parsed, list):
        raise ValueError(f"Parsed JSON is not a list of paths (got {type(parsed).__name__}).")

    return [item for item in parsed if isinstance(item, dict)]


def extract_paths(
    text: str,
    llm: Callable[[str], dict] | None = None,
    schema: dict | None = None,
    source_chunk_id: str | None = None,
) -> list[dict]:
    """Extract semantic paths from `text` via an LLM callable.

    Args:
        text: the text to extract paths from
        llm: an injected callable llm(prompt: str) -> dict that returns
            {"ok": bool, "text": str, "usage": {...}} or
            {"ok": False, "error": {...}}. When None, uses a default
            built lazily from agentic_ai.models.llm_handler.
        schema: optional dict with "schema_hint" and/or "relations" keys
        source_chunk_id: optional chunk id to attach to each extracted path

    Returns a list of validated path dicts (see `schema.validate_path`).
    Malformed paths returned by the LLM are dropped. Rejected paths and
    errors are not printed but could be collected by the caller if needed.

    Raises:
        RuntimeError: if the LLM callable returns ok=False
        ValueError: if the LLM's text output contains no parseable JSON array
    """
    if not isinstance(text, str) or not text.strip():
        return []

    if llm is None:
        # Lazy default: use the project's llm_handler
        from agentic_ai.models import llm_handler
        llm = lambda p: llm_handler.call_llm(
            [{"role": "user", "content": p}],
            role="extractor",
            temperature=0,
            max_completion_tokens=1500
        )

    prompt = build_extraction_prompt(text, schema=schema)
    response = llm(prompt)

    if not isinstance(response, dict):
        raise RuntimeError(f"LLM callable returned non-dict: {type(response).__name__}")

    if response.get("ok") is False:
        error = response.get("error", {})
        error_code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        error_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise RuntimeError(f"LLM extraction failed: [{error_code}] {error_msg}")

    raw_output = response.get("text")
    if not isinstance(raw_output, str):
        raise ValueError(
            f"Expected LLM callable to return a 'text' field, got {type(raw_output).__name__}"
        )

    raw_paths = _parse_json_paths(raw_output)

    if source_chunk_id is not None:
        for raw_path in raw_paths:
            raw_path["source_chunk_id"] = source_chunk_id

    valid_paths, rejected = validate_paths(raw_paths)

    return valid_paths


def build_batch_prompt(batch: list[dict], schema: dict | None = None) -> str:
    """The extraction prompt for several chunks at once: each chunk is labelled, and every path must
    name the chunk it came from, so batching never loses attribution."""
    labelled = "\n\n".join(f"[chunk {c['id']}]\n{c['text']}" for c in batch)
    return build_extraction_prompt(labelled, schema=schema) + (
        "\nThe text is made of chunks, each starting with a [chunk <id>] label. Every path object must also "
        'have "source_chunk_id": the <id> of the one chunk its evidence comes from (paths never span chunks). '
        "Write source, relation and target in the language of the text.\n")


def extract_paths_batch(
    chunks: list[dict],
    llm: Callable[[str], dict] | None = None,
    schema: dict | None = None,
    max_chars: int = 6000,
) -> tuple[dict[str, list[dict]], dict]:
    """Extract semantic paths from a batch of chunks, grouping by token budget.

    Args:
        chunks: list of {"id": str, "text": str} dicts
        llm: injected LLM callable (see extract_paths)
        schema: optional schema dict
        max_chars: maximum total characters per LLM call

    Returns:
        (result_dict, stats) where:
        - result_dict: {chunk_id: [paths]} with an entry for EVERY input chunk_id
          (may be [] if extraction failed or returned no paths)
        - stats: {"calls": int, "tokens": int, "rejected": int, "errors": int,
          "failed_ids": [chunk_ids where extraction failed]}

    Raises:
        ValueError: if chunks is not a list of dicts with "id" and "text" fields
    """
    if llm is None:
        from agentic_ai.models import llm_handler
        llm = lambda p: llm_handler.call_llm(
            [{"role": "user", "content": p}],
            role="extractor",
            temperature=0,
            max_completion_tokens=1500
        )

    # Validate input
    if not isinstance(chunks, list):
        raise ValueError(f"chunks must be a list, got {type(chunks).__name__}")

    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError(f"each chunk must be a dict, got {type(chunk).__name__}")
        if "id" not in chunk or "text" not in chunk:
            raise ValueError(f"each chunk must have 'id' and 'text' fields: {chunk}")

    # Group chunks into batches
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_size = 0

    for chunk in chunks:
        chunk_size = len(chunk["text"])
        if current_batch and current_size + chunk_size > max_chars:
            # Start a new batch
            batches.append(current_batch)
            current_batch = [chunk]
            current_size = chunk_size
        else:
            current_batch.append(chunk)
            current_size += chunk_size

    if current_batch:
        batches.append(current_batch)

    # Process each batch
    result: dict[str, list[dict]] = {chunk["id"]: [] for chunk in chunks}
    stats = {
        "calls": 0,
        "tokens": 0,
        "rejected": 0,
        "errors": 0,
        "failed_ids": [],
    }

    for batch in batches:
        # Build prompt for this batch
        prompt = build_batch_prompt(batch, schema=schema)

        # Call LLM
        try:
            response = llm(prompt)
        except Exception:
            # LLM call failed
            stats["errors"] += 1
            for chunk in batch:
                stats["failed_ids"].append(chunk["id"])
            continue

        if not isinstance(response, dict) or response.get("ok") is False:
            # LLM returned an error
            stats["errors"] += 1
            for chunk in batch:
                stats["failed_ids"].append(chunk["id"])
            continue

        # Track tokens
        usage = response.get("usage", {})
        if isinstance(usage, dict):
            stats["tokens"] += usage.get("total_tokens", 0)
        stats["calls"] += 1

        # Parse response
        raw_output = response.get("text", "")
        try:
            raw_paths = _parse_json_paths(raw_output)
        except ValueError:
            # Parsing failed
            stats["errors"] += 1
            for chunk in batch:
                stats["failed_ids"].append(chunk["id"])
            continue

        # Filter paths by source_chunk_id; with one chunk in the call there is no doubt where a path came from
        batch_ids = {c["id"] for c in batch}
        for raw_path in raw_paths:
            if len(batch) == 1 and not raw_path.get("source_chunk_id"):
                raw_path["source_chunk_id"] = batch[0]["id"]
            source_chunk_id = str(raw_path.get("source_chunk_id", "")).strip().removeprefix("chunk ").strip()
            raw_path["source_chunk_id"] = source_chunk_id
            if source_chunk_id not in batch_ids:
                stats["rejected"] += 1
                continue

            valid_paths, rejected = validate_paths([raw_path])
            if rejected:
                stats["rejected"] += len(rejected)
            else:
                result[source_chunk_id].extend(valid_paths)

    return result, stats
