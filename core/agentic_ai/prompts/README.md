# prompts/

Prompt templates and system instructions, one markdown file per prompt. The workflow yaml names them
(`prompts: {system: file:system.md}`); `agentic_ai.prompts.load` reads them. `{key}` placeholders are
filled by the runtime from the session state ({task}, {tools}, {observation}, ...).

| file | used by |
|---|---|
| `system.md` | the agent (actor), first message of every session |
| `reviewer.md` | the separate reviewer that judges a finished answer |
