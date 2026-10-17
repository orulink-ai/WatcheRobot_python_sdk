"""Run the workspace SDK source; all behavior belongs to the SDK entry point."""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from watcherobot.runtime.daemon.__main__ import main as sdk_main

    return sdk_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
