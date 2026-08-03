"""Standalone frozen entry for the SDK-owned Desktop Runtime."""

from __future__ import annotations

import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path

def run_application(application_dir: Path) -> int:
    """Validate and execute one managed Application from its own directory."""

    from watcherobot.runtime.daemon.application.manifest import ApplicationManifest

    application_root = Path(application_dir).resolve()
    manifest = ApplicationManifest.load(application_root)
    previous_cwd = Path.cwd()
    previous_path = list(sys.path)
    try:
        os.chdir(application_root)
        sys.path.insert(0, str(application_root))
        runpy.run_path(str(manifest.entrypoint), run_name="__main__")
    finally:
        sys.path[:] = previous_path
        os.chdir(previous_cwd)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Route the frozen executable to Daemon or Application execution."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--maintenance-esptool":
        try:
            import esptool
        except ImportError as exc:
            raise SystemExit("esptool is not installed in the Desktop Runtime") from exc
        esptool.main(args[1:])
        return 0
    if args and args[0] == "--application":
        if len(args) != 2 or not args[1].strip():
            raise SystemExit("--application requires an Application directory")
        return run_application(Path(args[1]))

    from watcherobot.runtime.daemon.__main__ import main as daemon_main

    return daemon_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
