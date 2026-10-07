# AI-Blue

An independent Agentic AI architecture: a reusable core (`agentic_ai`) with
application, launch, and deployment layers built around it.

**Status:** repository skeleton only. See [`dev_mem/status_update.md`](dev_mem/status_update.md).

## Repository Architecture

| Directory | Role |
|---|---|
| `dev_mem/` | Developer / coding-agent project memory (vision, status, ADRs, session notes) |
| `core/agentic_ai/` | Reusable Agentic AI core, imported as `agentic_ai` |
| `backend/` | API / application-service layer; consumes the core |
| `frontend/` | User interface; talks only to the backend |
| `launcher/` | Startup and entrypoints |
| `config/` | Configuration defaults and templates (no secrets) |
| `deploy/` | Docker, Nginx, deployment docs |
| `tests/` | Unit, integration, runtime, tool, agent, evaluation tests |
| `docs/` | Human-facing architecture and development documentation |
| `scripts/` | Developer and operations tooling |

Dependency direction: `frontend → backend → agentic_ai`; the core never depends on outer layers.

Deeper material: [`dev_mem/project_vision.md`](dev_mem/project_vision.md) and
[`docs/architecture/`](docs/architecture/README.md). Coding agents: start at [`AGENTS.md`](AGENTS.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
