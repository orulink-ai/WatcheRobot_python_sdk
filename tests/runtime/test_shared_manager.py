"""Real isolated SDK/Desktop-manager interoperability, without user ports."""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from watcherobot import cli
from watcherobot.runtime.manager import ensure_command


@pytest.mark.parametrize("sdk_first", [True, False])
def test_shared_launch_orders_and_registered_project(tmp_path, monkeypatch, sdk_first):
    for key in ("STATE_ROOT", "INSTANCE_ROOT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, str(tmp_path / key))
    for key in ("CONTROL_PORT", "EXTERNAL_PORT", "PAIRING_PORT", "PREVIEW_UDP_PORT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, "0")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2] / "src"))
    command = [
        sys.executable,
        "-m",
        "watcherobot.runtime.daemon",
        "--managed-app-root",
        str(tmp_path / "desktop-store"),
    ]
    try:
        if sdk_first:
            first, _ = cli.ensure_runtime()
            ensure_command(command)
        else:
            ensure_command(command)
            first, _ = cli.ensure_runtime()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(ensure_command, command),
                executor.submit(cli.ensure_runtime),
            ]
            for future in futures:
                future.result()
        assert cli._live_runtime_state().pid == first.pid
        project = tmp_path / "project"
        project.mkdir()
        (project / "app.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "test.shared",
                    "name": "Shared",
                    "version": "1.0.0",
                    "requires_watcherobot": ">=0.1,<0.2",
                    "dependencies": [],
                }
            ),
            encoding="utf-8",
        )
        (project / "app.py").write_text(
            "import asyncio\nfrom watcherobot.application import ApplicationContext\n"
            "async def main():\n    async with ApplicationContext.from_environment():\n        await asyncio.sleep(60)\nasyncio.run(main())\n",
            encoding="utf-8",
        )
        cli._request_json(
            first.control_url,
            "/daemon/application/select",
            method="POST",
            payload={
                "application_dir": str(project),
                "launcher": {"kind": "python", "executable": sys.executable},
            },
        )
        cli._request_json(
            first.control_url, "/daemon/application/start", method="POST", timeout=20
        )
        assert ensure_command(command, activate=True) is True
        assert cli._live_runtime_state().pid == first.pid
        # Explicit reload stops the active Application and replaces the instance.
        assert ensure_command(command, activate=True, force=True) is False
        assert cli._live_runtime_state().pid != first.pid
        pointer = tmp_path / "INSTANCE_ROOT" / "current-launcher.json"
        previous = pointer.read_bytes()
        with pytest.raises(ValueError, match="validation failed"):
            ensure_command([str(tmp_path / "missing-python")], activate=True)
        assert pointer.read_bytes() == previous
        assert cli._live_runtime_state() is not None
    finally:
        cli.stop_runtime()


def test_real_version_takeover_and_downgrade(tmp_path, monkeypatch):
    """Exercise real process replacement without changing the checked-out version."""
    from watcherobot import __version__
    from watcherobot.runtime.manager import stop_shared_runtime

    for key in ("STATE_ROOT", "INSTANCE_ROOT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, str(tmp_path / key))
    for key in ("CONTROL_PORT", "EXTERNAL_PORT", "PAIRING_PORT", "PREVIEW_UDP_PORT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, "0")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2] / "src"))
    import shutil

    source = Path(__file__).resolve().parents[2] / "src"
    old_source = tmp_path / "old-src"
    shutil.copytree(
        source / "watcherobot",
        old_source / "watcherobot",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    version_file = old_source / "watcherobot/__init__.py"
    version_file.write_text(
        version_file.read_text(encoding="utf-8").replace(__version__, "0.0.1"),
        encoding="utf-8",
    )
    current = [sys.executable, "-m", "watcherobot.runtime.daemon"]
    old = current
    monkeypatch.setenv("PYTHONPATH", str(old_source))
    try:
        assert ensure_command(old, activate=True) is False
        first = cli._live_runtime_state()
        monkeypatch.setenv("PYTHONPATH", str(source))
        assert ensure_command(current, activate=True) is False
        second = cli._live_runtime_state()
        assert first.pid != second.pid
        assert (
            cli._request_json(second.control_url, "/daemon/status")["runtime"][
                "sdk_version"
            ]
            == __version__
        )
        assert ensure_command(current, activate=True) is True
        assert cli._live_runtime_state().pid == second.pid
        monkeypatch.setenv("PYTHONPATH", str(old_source))
        assert ensure_command(old, activate=True) is False
        third = cli._live_runtime_state()
        assert third.pid != second.pid
        assert (
            cli._request_json(third.control_url, "/daemon/status")["runtime"][
                "sdk_version"
            ]
            == "0.0.1"
        )
    finally:
        stop_shared_runtime()
