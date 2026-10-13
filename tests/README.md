# tests/

| Directory | Scope |
|---|---|
| `unit/` | Isolated unit tests; includes architectural boundary checks |
| `integration/` | Cross-component tests (backend ↔ core, integrations) |
| `runtime/` | Execution-engine tests |
| `tools/` | Tool interface and individual tool tests |
| `agent/` | Agent definition and behaviour tests |
| `evaluation/` | Deterministic evaluator tests |

Run: `pytest` from the repository root (configured in `pyproject.toml`; `core/` is on
the path, so no install is required). Uses `--import-mode=importlib`, so test
directories do not need `__init__.py`.
