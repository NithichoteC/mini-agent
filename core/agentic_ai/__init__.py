"""agentic_ai — the reusable Agentic AI core of AI-Blue.

This package is the architectural centre of the repository. It must remain
usable without the backend, frontend, launcher, or deployment layers, and it
must never import from them (see ADR-003 in dev_mem/architecture_decisions.md).

Subpackages (extension points; mostly unimplemented at this stage):

    agent/          agent definitions, composition, capability declarations
    runtime/        execution engine: lifecycle, steps, orchestration
    models/         LLM/model-provider adapters (provider abstraction)
    tools/          tool interface, registry, execution wrapper
    memory/         RUNTIME agent/task/session memory (not dev_mem/)
    messages/       shared schemas: requests, responses, tool calls, events
    tracing/        execution traces, observability, replay support
    evaluation/     deterministic evaluators, trace evaluation, scoring
    interfaces/     abstract contracts shared across subsystems
    integrations/   adapters to the external world (web, db, KG, services)

Non-code asset directories:

    prompts/          prompt templates and system instructions
    agent_knowledge/  curated knowledge resources bound to agents
    assets/           other static resources used by the core
"""
