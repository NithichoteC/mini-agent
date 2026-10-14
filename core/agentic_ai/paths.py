"""The project root: where relative paths in the config (config/runtime.yaml, config/tools.json, .env)
resolve. Running from the repository it is the folder that holds core/; AIBLUE_ROOT overrides it when
the package is installed elsewhere."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("AIBLUE_ROOT") or Path(__file__).resolve().parents[2])


def resolve(path) -> Path:
    """A config path: absolute as given, relative against ROOT."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
