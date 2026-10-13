"""Prompt templates (ADR-005): one markdown file per prompt in this folder, named after its purpose.
A workflow yaml names them under `prompts:` as `file:<name>.md`; a plain string there is the prompt
itself (handy for a one-off workflow). Templates use {key} placeholders filled by runtime.loop.render."""
from pathlib import Path

from agentic_ai.paths import resolve as resolve_path

DIR = Path(__file__).resolve().parent
PREFIX = "file:"


def load(value: str) -> str:
    """`file:system.md` -> that file's text (a bare name is looked up here, a path against the project
    root); anything else is returned as it is."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    name = value[len(PREFIX):].strip()
    path = DIR / name if "/" not in name else resolve_path(name)
    if not path.is_file():
        raise ValueError(f"prompt file '{name}' not found (looked in {path.parent})")
    return path.read_text()


def resolve_all(prompts: dict | None) -> dict:
    return {key: load(value) for key, value in (prompts or {}).items()}
