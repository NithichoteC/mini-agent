"""Execution engine.

Future responsibilities: execution lifecycle, orchestration, step execution,
tool and model invocation, state transitions, runtime control. The runtime
talks to providers only through ``models/`` and to the outside world only
through ``tools/`` and ``integrations/``.
"""
