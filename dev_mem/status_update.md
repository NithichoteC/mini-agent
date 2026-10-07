# AI-Blue Development Status

## Current Milestone

M0 — Repository skeleton and developer memory established. No Agentic AI functionality implemented.

## Current Repository State

- Before this milestone the project folder was empty; no prior source, tests, config, or Git history existed.
- The folder is **not a Git repository** yet.
- Skeleton present: `core/agentic_ai/` (package with documented subpackages), `backend/`, `frontend/`,
  `launcher/`, `config/`, `deploy/{docker,nginx}/`, `tests/`, `docs/`, `scripts/`, `dev_mem/`.
- Packaging: `pyproject.toml` (setuptools, `requires-python >=3.10`, zero runtime dependencies,
  `pytest` as the only dev dependency). Installs `agentic_ai` and `launcher`.

## Implemented

- Core package namespace `agentic_ai` with subpackages: agent, runtime, memory, models, messages,
  tools, tracing, evaluation, interfaces, integrations/{web, database, knowledge_graph, external}.
  Each contains only a responsibility docstring.
- Asset directories with README: `prompts/`, `agent_knowledge/`, `assets/`.
- Boundary tests (`tests/unit/test_architecture_boundaries.py`): all core subpackages import;
  core imports no application layer; core imports no agent framework.
- `.gitignore`, `.env.example`, root `README.md`, `AGENTS.md` (+ `CLAUDE.md` pointer), docs stubs.

## In Progress

Nothing.

## Pending

- Interfaces for the core: messages/event schema, tool interface + registry, model-provider interface, trace record.
- Runtime execution engine.
- Backend (framework not chosen), frontend scaffold, launcher entrypoints, Docker/Nginx files.
- Configuration format and loader.

## Known Issues

- Not under version control.
- `interfaces/` overlaps conceptually with `messages/` and with per-subsystem base classes; its exact scope is undecided.
- `models/` means model *providers*; risk of confusion with data models.
- Backend is documentation-only and not packaged until its framework is chosen.

## Important Technical Constraints

- `agentic_ai` must not import `backend`, `frontend`, or `launcher` (enforced by test).
- No LangChain / CrewAI / AutoGen / Semantic Kernel or similar.
- No secrets in the repository; `.env` is git-ignored; never commit `.venv/`.
- Verified on Python 3.10; minimum version not yet formally decided.

## Next Recommended Actions

1. `git init` and make the initial commit (owner decision).
2. Define `messages/` schemas and the trace record format together — runtime, tools, and tracing all depend on them.
3. Then define tool interface/registry and model-provider interface against those schemas.

## Last Updated

2026-10-07 — repository skeleton created.
