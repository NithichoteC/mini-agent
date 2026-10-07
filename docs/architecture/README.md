# Architecture overview

Explanatory reference. The authoritative *why* is in
[`dev_mem/project_vision.md`](../../dev_mem/project_vision.md); recorded decisions are in
[`dev_mem/architecture_decisions.md`](../../dev_mem/architecture_decisions.md).

## Dependency direction

```
frontend ──HTTP──▶ backend/API ──import──▶ agentic_ai (core)
                                              │
              runtime · agent · tools · models · memory · messages
              tracing · evaluation · interfaces · integrations
launcher ──▶ backend or agentic_ai (depending on deployment mode)
deploy/  ──  packages and serves the above; no source code
config/  ──  data read by backend / launcher / core; no logic
```

Arrows point from dependent to dependency. Nothing points *into* `launcher`,
`backend`, or `frontend` from the core; `tests/unit/test_architecture_boundaries.py`
enforces this for Python imports.

## Core subsystem map

| Subpackage | Responsibility |
|---|---|
| `agent/` | Agent definitions, composition, capability declarations |
| `agent_knowledge/` | Static curated knowledge for agents (not code, not memory) |
| `prompts/` | Prompt templates and system instructions |
| `runtime/` | Execution lifecycle, steps, orchestration, state transitions |
| `models/` | Model-provider adapters behind one interface |
| `messages/` | Shared request/response/tool-call/event schemas |
| `tools/` | Tool interface, registry, permissions, execution wrapper |
| `memory/` | Runtime agent/session/task memory (≠ `dev_mem/`) |
| `tracing/` | Execution traces for debugging, replay, audit |
| `evaluation/` | Deterministic evaluators over traces |
| `interfaces/` | Shared abstract contracts |
| `integrations/` | Web, database, knowledge-graph, external-service adapters |
