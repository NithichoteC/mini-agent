# dev_mem — Development Memory

Persistent project memory for humans and coding agents (Claude, Codex, and others)
working on AI-Blue across many sessions.

> `dev_mem/` is **developer/project memory**. It is unrelated to
> `core/agentic_ai/memory/`, which is **runtime memory of executing agents**.

## Files

| File | Holds | Changes |
|---|---|---|
| `project_vision.md` | What AI-Blue is, why, boundaries, principles, non-goals | Rarely — only when direction genuinely changes |
| `status_update.md` | Current state of the repository | After any substantial work that changes project state |
| `architecture_decisions.md` | Lightweight ADRs for decisions actually made | When a real architectural decision is made |
| `session_notes/` | Historical notes from significant sessions | Only when a session produced knowledge worth keeping |

`status_update.md` is the **present**; `session_notes/` is the **past**. Keep them distinct.

## Protocol

**At the start of substantial work**, read in order:

1. `project_vision.md`
2. `status_update.md`
3. The ADRs in `architecture_decisions.md` relevant to the area you will touch

Do not make an important architectural change that contradicts an Accepted ADR
without first proposing a superseding ADR.

**At the end of substantial work:**

- Update `status_update.md` if project state materially changed (rewrite to current state; do not append a log).
- Add an ADR only if an actual architectural decision was made. Never record speculative or invented decisions.
- Add a session note only if the session produced knowledge worth preserving.
- Modify `project_vision.md` only if project direction genuinely changed.

Trivial interactions (typo fixes, small edits) require no dev_mem update.
