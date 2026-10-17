"""Keep a bundled rescue entry while dispatching management to the selected SDK."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import Manager


def prepare_request(manager: Manager) -> dict[str, Any]:
    request = dict(manager.request)
    if "_bootstrap" not in request:
        baseline = manager.baseline()
        request["_bootstrap"] = {
            "baseline": baseline,
            "commands": {
                "daemon": manager.command(baseline),
                "distribution": manager.command(baseline, entry="distribution"),
            },
        }
    return request


def forward_management(
    manager: Manager, *, output: Callable[[dict[str, Any]], None] | None = None
) -> int | None:
    """Use the selected SDK's management implementation; broken SDKs use rescue."""
    if manager.request.get("_bootstrap") or manager.request.get("action") == "repair":
        return None
    try:
        selected = manager.selection()
    except (OSError, ValueError):
        return None
    if selected["kind"] != "environment":
        return None
    if Path(selected["root"]).resolve() == Path(sys.prefix).resolve():
        return None
    from .installer import environment_python

    command = [
        str(environment_python(Path(selected["root"]))),
        "-I",
        "-m",
        "watcherobot.management",
    ]
    try:
        probe = subprocess.run(
            [command[0], "-I", "-c", "from watcherobot.management.cli import main"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if probe.returncode:
            raise ValueError(
                "Selected SDK management entry cannot be imported; repair the runtime"
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        manager.request["_selected_manager_error"] = str(exc)
        return None
    # Relay rather than inherit stdout: a persistent management host must keep
    # each selected SDK response associated with its original request.
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    found_result = False
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(
            json.dumps(prepare_request(manager), ensure_ascii=False).encode("utf-8")
        )
        process.stdin.close()
        while raw := process.stdout.readline(1024 * 1024 + 1):
            if len(raw) > 1024 * 1024:
                raise ValueError("Selected SDK management response exceeds limit")
            message = json.loads(raw)
            if (
                not isinstance(message, dict)
                or message.get("type") not in {"progress", "result"}
                or found_result
            ):
                raise ValueError("Selected SDK management response is invalid")
            found_result = message["type"] == "result"
            if output is not None:
                output(message)
            else:
                print(json.dumps(message, ensure_ascii=False), flush=True)
        status = process.wait()
        if not found_result:
            raise ValueError("Selected SDK manager exited without a result")
        return status
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def distribution_command() -> list[str] | None:
    """Resolve SDK-owned distribution operations without starting a Daemon."""
    from .service import Manager

    manager = Manager(
        {
            "desktop": {
                "manager": ">=1,<2",
                "management": ">=1,<2",
                "distribution": ">=1,<2",
            }
        }
    )
    selected = manager.selection()
    if (
        selected["kind"] != "environment"
        or Path(selected["root"]).resolve() == Path(sys.prefix).resolve()
    ):
        return None
    manager._require_supported()
    return manager.command(selected, entry="distribution")
