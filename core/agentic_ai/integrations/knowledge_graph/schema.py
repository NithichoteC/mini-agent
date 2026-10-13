"""Semantic path schema and validation.

A "path" is the basic unit of `graph_library`:

    {
        "source": "Q3 outage",
        "relation": "delayed",
        "target": "Project Phoenix",
        "evidence": "After the Q3 outage, Project Phoenix was delayed.",
        "confidence": 0.82,
        "source_chunk_id": "chunk_002",
    }

This module does one job: decide whether a path dict is well-formed, and
produce a cleaned/defaulted copy when it is. It does not call an LLM, build a
graph, or do retrieval.
"""

from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = ("source", "relation", "target")
OPTIONAL_FIELDS = ("evidence", "confidence", "source_chunk_id")
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

DEFAULT_CONFIDENCE = 0.5


def validate_path(path: Any) -> tuple[dict | None, str | None]:
    """Validate a single raw path dict.

    Returns ``(cleaned_path, None)`` if valid, or ``(None, reason)`` if the
    path is malformed. Never raises -- callers decide what to do with
    rejected paths (this module never silently keeps a bad path, but it also
    never silently drops information about *why* a path was rejected).

    Required: ``source``, ``relation``, ``target`` must be non-empty strings.
    Optional: ``evidence`` (str, default ``""``), ``confidence`` (numeric,
    default 0.5), ``source_chunk_id`` (str or None, default None).
    """
    if not isinstance(path, dict):
        return None, f"path must be a dict, got {type(path).__name__}"

    cleaned: dict[str, Any] = {}

    for field in REQUIRED_FIELDS:
        value = path.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, f"'{field}' must be a non-empty string (got {value!r})"
        cleaned[field] = " ".join(value.split())

    evidence = path.get("evidence", "")
    if evidence is None:
        evidence = ""
    if not isinstance(evidence, str):
        evidence = str(evidence)
    cleaned["evidence"] = evidence

    confidence = path.get("confidence", DEFAULT_CONFIDENCE)
    if confidence is None:
        confidence = DEFAULT_CONFIDENCE
    try:
        cleaned["confidence"] = float(confidence)
    except (TypeError, ValueError):
        return None, f"'confidence' must be numeric (got {confidence!r})"

    source_chunk_id = path.get("source_chunk_id")
    if source_chunk_id is not None and not isinstance(source_chunk_id, str):
        source_chunk_id = str(source_chunk_id)
    cleaned["source_chunk_id"] = source_chunk_id

    return cleaned, None


def validate_paths(paths: list[Any]) -> tuple[list[dict], list[dict]]:
    """Validate a list of raw paths.

    Returns ``(valid_paths, rejected)`` where ``rejected`` is a list of
    ``{"path": <original>, "reason": <str>}`` so malformed input is never
    silently dropped without a trace.
    """
    valid: list[dict] = []
    rejected: list[dict] = []
    for raw_path in paths:
        cleaned, reason = validate_path(raw_path)
        if cleaned is None:
            rejected.append({"path": raw_path, "reason": reason})
        else:
            valid.append(cleaned)
    return valid, rejected
