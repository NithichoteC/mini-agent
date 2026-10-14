"""Execution engine: loop.py runs the workflow steps (act / llm / stop_if), validates each action
against the tool registry, applies permissions and records every step; sandbox.py runs shell commands
in a workspace with a timeout and a scrubbed environment."""
