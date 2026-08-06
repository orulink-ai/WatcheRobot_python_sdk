from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from watcherobot import cli as watcherobot_cli
from watcherobot.cli import main
from watcherobot.distribution.install import InstalledApplication


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


def test_windows_background_daemon_uses_pythonw_redirector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    python = scripts_dir / "python.exe"
    pythonw = scripts_dir / "pythonw.exe"
    python.write_bytes(b"python redirector")
    pythonw.write_bytes(b"pythonw redirector")
    monkeypatch.setattr(watcherobot_cli.os, "name", "nt")

    assert watcherobot_cli._background_python_executable(python) == pythonw.resolve()


def test_cli_starts_reuses_and_stops_the_unique_runtime(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PAIRING_PORT", "0")

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

    try:
        assert main(["app", "run", str(application_dir)]) == 0
        run_output = capsys.readouterr().out
        assert f"Running Application: {application_dir.resolve()}" in run_output
        assert "Press Ctrl+C to stop." in run_output
        assert "Application finished: ended" in run_output
        assert main(["daemon", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["running"] is True
        assert status["application"]["current_app"] == "cli_test_app"
        assert status["application"]["state"] == "ended"
    finally:
        main(["daemon", "stop"])
        capsys.readouterr()


def test_cli_runs_installed_application_in_an_isolated_store_runtime(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    store_root = tmp_path / "application-store"
    app_root = store_root / "apps" / "com.example.demo"
    application = InstalledApplication(
        application_id="com.example.demo",
        name="Installed Demo",
        version="1.0.0",
        status="installed",
        application_root=app_root,
    )
    runtime_calls: list[dict[str, object]] = []
    requests: list[tuple[str, str, str, dict[str, object] | None]] = []

    def fake_ensure_runtime(**kwargs):
        runtime_calls.append(kwargs)
        return (
            type(
                "State",
                (),
                {
                    "control_url": "http://isolated-runtime",
                    "external_url": "ws://0.0.0.0:12345",
                },
            )(),
            False,
        )

    def fake_request_json(base_url, path, *, method="GET", payload=None, **_kwargs):
        requests.append((base_url, path, method, payload))
        if path == "/daemon/status":
            return {
                "application": {
                    "current_app": application.application_id,
                    "state": "ended",
                    "process_id": None,
                }
            }
        return {
            "application": {
                "current_app": application.application_id,
                "state": "running",
                "process_id": 123,
            }
        }

    monkeypatch.setattr(
        "watcherobot.cli.list_installed_applications",
        lambda root: (application,),
        raising=False,
    )
    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr("watcherobot.cli._request_json", fake_request_json)

    assert (
        main(
            [
                "app",
                "run-installed",
                "--store-root",
                str(store_root),
                "--app-id",
                application.application_id,
            ]
        )
        == 0
    )

    assert runtime_calls == [
        {
            "state_root": store_root / ".daemon-session",
            "managed_app_root": store_root,
            "ephemeral_ports": True,
        }
    ]
    assert requests[0] == (
        "http://isolated-runtime",
        "/daemon/application/select",
        "POST",
        {
            "application_dir": str(app_root / "source"),
            "launcher": {
                "kind": "python",
                "executable": str(
                    app_root
                    / ".venv"
                    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                ),
            },
        },
    )
    output = capsys.readouterr().out
    assert "Running installed Application: Installed Demo" in output
    assert f"Application store: {store_root}" in output
    assert "Daemon external URL: ws://0.0.0.0:12345" in output
    assert "Application finished: ended" in output


def test_cli_rejects_missing_or_broken_installed_application(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    store_root = tmp_path / "application-store"
    monkeypatch.setattr(
        "watcherobot.cli.list_installed_applications",
        lambda root: (),
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing Application started a Daemon")
        ),
    )

    assert (
        main(
            [
                "app",
                "run-installed",
                "--store-root",
                str(store_root),
                "--app-id",
                "com.example.missing",
            ]
        )
        == 2
    )
    assert "Installed Application was not found" in capsys.readouterr().err


def test_cli_packages_wapp_but_does_not_install_it_through_daemon_catalog(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    application_dir = tmp_path / "application"
    package_path = tmp_path / "cli-test.wapp"
    _write_application(application_dir)

    def fail_if_called():
        raise AssertionError("legacy catalog command started the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)

    assert main(
        [
            "app",
            "package",
            str(application_dir),
            str(package_path),
        ]
    ) == 0
    package_output = capsys.readouterr().out
    assert package_output == f"Application package created: {package_path}\n"

    assert main(["app", "run", str(package_path)]) == 2
    assert main(["app", "install", str(package_path)]) == 2
    assert main(["app", "list"]) == 2
    assert main(["app", "select", "cli_test_app"]) == 2
    assert main(["app", "uninstall", "cli_test_app"]) == 2

    errors = capsys.readouterr().err.splitlines()
    assert len(errors) == 5
    assert all("Watcher Desktop Application Store" in line for line in errors)
