# prompts/

Prompt templates and system instructions — first-class project assets.

Rules:
- Large prompt strings do not live inside Python code. Code loads them from here.
- One template per file; name files after their purpose, not their caller.
- Prompts are versioned with the repository so traces can reference the exact template used.

The file format (plain text, Markdown, Jinja-style, YAML with metadata) is **not yet decided**.
