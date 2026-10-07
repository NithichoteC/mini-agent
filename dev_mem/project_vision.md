# AI-Blue — Project Vision

Stable source of truth for what AI-Blue is and how it is shaped.
Not a progress log; see `status_update.md` for current state.

## What AI-Blue is

AI-Blue is an Agentic AI system built on its **own** architecture. Its centre is a
reusable Python core, `agentic_ai`, that provides agents, a runtime, tools, model-provider
adapters, runtime memory, message schemas, tracing, evaluation, and external integrations.
Applications (an API backend, a web frontend, launchers, deployments) are built around
that core and consume it.

## Why it exists / why our own architecture

- The core must be understood, controlled, and extended by the team, not inherited from a framework.
- Third-party agent frameworks (LangChain, CrewAI, AutoGen, Semantic Kernel, and similar) are
  not the architectural foundation. They are not introduced unless explicitly requested.
- Explicit, readable Python abstractions are preferred over framework magic.

## Long-term objective

A production-oriented, extensible Agentic AI platform in which new specialised agents and
tools are added without reorganising the repository, and whose executions are traceable,
replayable, deterministically evaluable, and auditable.

## Architectural philosophy

1. `core` is the reusable intelligence/runtime layer and the architectural centre.
2. The web application is not the centre; it is one consumer of the core.
3. The backend mediates between external applications and the core.
4. Runtime, tools, memory, tracing, prompts, and provider adapters are usable without the frontend.
5. Tracing and observability are first-class, designed in from the start, not added later.

## Major system boundaries

```
frontend  →  backend/API  →  agentic_ai core  →  runtime · agents · tools · models · memory · integrations
launcher  →  backend or core (by deployment mode)
deploy/   →  infrastructure only        config/  →  data only
```

The core never depends on backend, frontend, or launcher.

## Relationships between parts

- **Core runtime** executes agents: lifecycle, steps, orchestration, state transitions.
  It invokes models only through `models/` and the outside world only through `tools/` and `integrations/`.
- **Agents** are executable definitions with declared capabilities. Their prompts live in
  `prompts/`; their curated knowledge lives in `agent_knowledge/`. Definition, prompt, and knowledge are separate.
- **Tools** implement a common interface and are discovered through a registry, so new tools plug in.
- **Model providers** sit behind one abstraction; provider-specific code does not leak into the runtime.
- **Memory/state** is runtime memory of agents, sessions, and tasks (distinct from `dev_mem/`).
- **Messages** define the shared schemas (requests, responses, tool calls, observations, events) that tie runtime, tools, and tracing together.
- **Tracing** records what happened; **evaluation** judges traces deterministically.
- **Integrations** isolate web, database, knowledge-graph, and other external systems from the runtime.
- **Backend** exposes the core over an API (validation, auth boundary, sessions, streaming). Backend concerns stay out of the core.
- **Frontend** renders UI and calls the backend. It holds no reasoning, orchestration, or credentials.
- **Deployment** packages and serves the system; it contains no application source and no secrets.

## Core design principles

- Configuration is separate from executable logic; secrets are separate from configuration.
- Deployment artifacts are separate from source.
- Prompts are first-class assets, not strings scattered in code.
- Agent knowledge is separate from agent definitions.
- The architecture must support execution traces, deterministic evaluators, debugging,
  experiment replay, and potentially governance/audit.
- Tests cover unit, integration, runtime, agent, tool, and evaluator levels.
- Dependencies stay minimal.

## Explicit non-goals

- Building on a third-party agent framework.
- Putting agent logic, orchestration, or credentials in the frontend.
- Making the web application the architectural centre.
- Committing secrets, real credentials, or virtual environments.

## Important constraints

- Python for the core and (expected) the backend; React with JS/TS and Tailwind/CSS expected for the frontend.
- Development spans many sessions and multiple coding agents; `dev_mem/` exists to keep them aligned.
