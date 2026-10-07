# Architecture Decision Records

Lightweight ADRs. Record only decisions actually made. Supersede rather than edit
accepted decisions: add a new ADR and set the old one to `Superseded by ADR-NNN`.

---

## ADR-001 — Reusable core lives at `core/agentic_ai/`, imported as `agentic_ai`

Date: 2026-10-07
Status: Accepted

### Context
The project needs one reusable Python package for the Agentic AI core, independent of
backend, frontend, and deployment. Options: (A) `core/agentic_ai/`, or (B) a top-level `agentic/`.
The repository contained no existing code, so no prior layout constrained the choice.

### Decision
Use `core/agentic_ai/`. The import name is `agentic_ai`
(e.g. `from agentic_ai.runtime import ...`). `pyproject.toml` maps it with
`[tool.setuptools.packages.find] where = ["core", "."]`.

### Rationale
- `core/` acts like a `src/` layout: the package cannot be accidentally imported from the
  repository root without installation or an explicit path, which surfaces packaging errors early.
- It mirrors the conceptual architecture (`ai-blue → core → agentic_ai`) and leaves room for
  further core-level packages later without crowding the root.
- `agentic_ai` is a distinct, descriptive import name; `agentic` is more generic and more collision-prone.
- This was the stated owner preference; inspection found no technical reason to deviate.

### Consequences
- Tools need `core/` on the path: handled by `pip install -e .` or pytest's `pythonpath = ["core", "."]`.
- Directory name (`core`) and import name (`agentic_ai`) differ; documented in README and AGENTS.md.

---

## ADR-002 — Own Agentic AI architecture; no third-party agent framework as foundation

Date: 2026-10-07
Status: Accepted

### Context
Frameworks such as LangChain, CrewAI, AutoGen, and Semantic Kernel offer ready-made agent abstractions.

### Decision
AI-Blue builds its own architecture. None of these frameworks is the foundation, and none is
introduced unless explicitly requested later.

### Rationale
Explicit, understandable abstractions; full control over runtime, tracing, and evaluation semantics;
minimal dependencies.

### Consequences
More foundational code must be written in-house. Enforced for `agentic_ai` imports by
`tests/unit/test_architecture_boundaries.py`.

---

## ADR-003 — Dependency direction: frontend → backend → core

Date: 2026-10-07
Status: Accepted

### Context
The web application must not become the architectural centre, and the core must be reusable.

### Decision
`frontend` calls `backend` over the API; `backend` imports `agentic_ai`; `launcher` may invoke
backend or core depending on deployment mode. `agentic_ai` never imports backend, frontend, or launcher.
The frontend contains no agent reasoning, orchestration, or credentials.

### Rationale
Keeps the core usable from CLI, workers, tests, and other applications.

### Consequences
Anything the core needs from outside must arrive through its own interfaces (config, adapters),
not by importing outer layers. Enforced by test.

---

## ADR-004 — `dev_mem/` (developer memory) is separate from runtime agent memory

Date: 2026-10-07
Status: Accepted

### Context
The project is developed across many sessions by multiple coding agents, and the core will also
have runtime memory. The word "memory" is ambiguous.

### Decision
`dev_mem/` holds developer/project memory (vision, current status, ADRs, session notes) with the
protocol in `dev_mem/README.md`. `core/agentic_ai/memory/` holds runtime agent/session/task memory only.

### Consequences
Agents entering the repo are pointed to `dev_mem/` via `AGENTS.md`/`CLAUDE.md`. Neither location
may store the other's content.

---

## ADR-005 — Prompts and agent knowledge are first-class assets, separate from code

Date: 2026-10-07
Status: Accepted

### Decision
Prompt templates live in `agentic_ai/prompts/`; curated agent knowledge lives in
`agentic_ai/agent_knowledge/`; executable agent definitions live in `agentic_ai/agent/`.
Large prompt strings are not embedded in Python code. Both asset directories ship as package data.

### Consequences
A loading mechanism and file format are still required (not yet decided).

---

## ADR-006 — Configuration, secrets, and deployment are separated from source

Date: 2026-10-07
Status: Accepted

### Decision
- `config/` holds version-controlled defaults/templates only.
- Environment variables via `.env` (git-ignored), templated by `.env.example`.
- Secrets are never committed; local overrides (`config/local/`, `*.local.*`) are git-ignored.
- `deploy/` holds Docker/Nginx/deployment material and no application source or secrets.
- Virtual environments are developer-local and never committed.

---

## ADR-007 — Tracing is a first-class core subsystem

Date: 2026-10-07
Status: Accepted

### Context
The system must later support execution traces, deterministic evaluation, debugging,
experiment replay, and potentially governance/audit.

### Decision
`agentic_ai/tracing/` is a dedicated subsystem from the start, and `agentic_ai/evaluation/`
is reserved for evaluators.

### Consequences
Trace record design is not yet made, but runtime, tool, and model interfaces should be designed
to emit trace events rather than have tracing retrofitted.
