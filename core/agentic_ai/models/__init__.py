"""Model / LLM provider abstraction.

Provider adapters (e.g. OpenAI, Anthropic, Groq, Ollama, vLLM, local models)
will live here behind a common interface so that provider-specific code does
not leak into the runtime.

Note: "models" here means *model providers*, not data models. Data schemas
belong in ``messages/``.
"""
