"""Entry point: python main.py <command> ...  (launcher/cli.py has the commands)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

from launcher.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
