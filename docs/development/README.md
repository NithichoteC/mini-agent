# Development guide

## Setup

```bash
python3 -m venv .venv          # git-ignored; never commit a virtualenv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

`pytest` also works without installing, because `pyproject.toml` puts `core/` on the test path.

## Working protocol

Before substantial work, read `dev_mem/README.md` and follow its protocol.
Secrets go in `.env` (copied from `.env.example`), never in Git.
