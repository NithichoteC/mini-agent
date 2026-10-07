# Instructions for coding agents

Before substantial work in this repository, read and follow [`dev_mem/README.md`](dev_mem/README.md).
It defines what to read first (`project_vision.md`, `status_update.md`, relevant ADRs)
and what to update when you finish.

Hard rules:
- `core/agentic_ai` must never import `backend`, `frontend`, or `launcher`.
- Do not introduce LangChain, CrewAI, AutoGen, Semantic Kernel, or similar frameworks.
- No secrets in the repository. No committed virtualenvs.
- `dev_mem/` is developer memory; `core/agentic_ai/memory/` is runtime agent memory. Do not mix them.
