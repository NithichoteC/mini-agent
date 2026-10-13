"""Launcher — application startup and entrypoints.

Lets users and operators start AI-Blue without invoking low-level modules
directly. Future roles may include local development startup, backend
startup, a CLI, worker processes, and environment selection.

The launcher sits at the top of the dependency graph: it may import the
backend or ``agentic_ai`` depending on deployment mode; nothing imports it.
"""
