# config/

Version-controlled configuration **defaults and templates** only. No secrets.

| Kind | Where | In Git? |
|---|---|---|
| Defaults / templates | `config/*.toml` (or `.yaml`) | yes |
| Local machine overrides | `config/local/`, `*.local.toml` | no (git-ignored) |
| Environment variables | `.env` (template: `/.env.example`) | no |
| Secrets (API keys, DB passwords, tokens) | environment / secret manager | never |

Configuration is data. Executable logic that *reads* configuration belongs in code
(`agentic_ai`, backend, launcher), not here.

Format (TOML vs YAML) and the loader location are not yet decided.
