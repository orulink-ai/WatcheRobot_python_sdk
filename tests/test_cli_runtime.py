from __future__ import annotations

import json
import time
from pathlib import Path

from watcherobot.cli import main


COMPLETING_APPLICATION = """
import asyncio

from watcherobot.application import ApplicationContext


async def main():
    async with ApplicationContext.from_environment() as app:
        app.logger.info("managed application entered")
        await asyncio.sleep(0.05)


asyncio.run(main())
"""


def _write_application(root: Path) -> None:
    root.mkdir(parents=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "cli_test_app",
                "name": "CLI Test Application",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(
        COMPLETING_APPLICATION,
        encoding="utf-8",
    )


def test_cli_status_reports_not_running_for_empty_user_state(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path))

    exit_code = main(["daemon", "status"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {"running": False}


def test_cli_starts_reuses_and_stops_the_unique_runtime(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PAIRING_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PREVIEW_UDP_PORT", "0")

    try:
        assert main(["daemon", "start"]) == 0
        started = json.loads(capsys.readouterr().out)
        assert started["running"] is True
        assert started["reused"] is False

        assert main(["daemon", "start"]) == 0
        reused = json.loads(capsys.readouterr().out)
        assert reused["running"] is True
        assert reused["reused"] is True

        assert main(["daemon", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["running"] is True
    finally:
        assert main(["daemon", "stop"]) == 0
        capsys.readouterr()

    for _ in range(100):
        if main(["daemon", "status"]) == 1:
            break
        capsys.readouterr()
        time.sleep(0.02)
    assert json.loads(capsys.readouterr().out) == {"running": False}


def test_cli_runs_managed_application_and_leaves_runtime_alive(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    state_root = tmp_path / "runtime"
    application_dir = tmp_path / "application"
    _write_application(application_dir)
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(state_root))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PAIRING_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PREVIEW_UDP_PORT", "0")

    try:
        assert main(["app", "run", str(application_dir)]) == 0
        assert main(["daemon", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["running"] is True
        assert status["application"]["current_app"] == "cli_test_app"
        assert status["application"]["state"] == "ended"
    finally:
        main(["daemon", "stop"])
        capsys.readouterr()


def test_cli_packages_installs_and_runs_wapp_from_catalog(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    state_root = tmp_path / "runtime"
    application_dir = tmp_path / "application"
    package_path = tmp_path / "cli-test.wapp"
    _write_application(application_dir)
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(state_root))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PAIRING_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PREVIEW_UDP_PORT", "0")

    try:
        assert main(
            [
                "app",
                "package",
                str(application_dir),
                str(package_path),
            ]
        ) == 0
        capsys.readouterr()
        assert main(["app", "run", str(package_path)]) == 0
        assert main(["app", "list"]) == 0
        installed = json.loads(capsys.readouterr().out)
        assert installed["applications"][0]["id"] == "cli_test_app"
    finally:
        main(["daemon", "stop"])
        capsys.readouterr()
