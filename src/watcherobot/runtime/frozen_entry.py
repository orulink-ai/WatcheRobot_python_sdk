"""Standalone frozen entry for the SDK-owned Desktop Runtime."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Route the frozen executable to the Daemon or maintenance tooling."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--maintenance-esptool":
        try:
            import esptool  # type: ignore[import-untyped]
        except ImportError as exc:
            raise SystemExit("esptool is not installed in the Desktop Runtime") from exc
        try:
            esptool.main(args[1:])
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # esptool reports expected serial failures as exceptions
            print(f"esptool failed: {exc}", file=sys.stderr)
            return 2
        return 0
    from watcherobot.runtime.daemon.__main__ import main as daemon_main

    return daemon_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
