from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from watcherobot import cli as watcherobot_cli
from watcherobot.cli import main
from watcherobot.distribution.install import InstalledApplication
from watcherobot.runtime.daemon.instance import (
    RuntimeProcessState,
    RuntimeStateStore,
    runtime_instance_id,
)


COMPLETING_APPLICATION = """
import asyncio

from watcherobot.application import ApplicationContext


async def main():
    async with ApplicationContext.from_environment() as app:
        app.logger.info("managed application entered")
        await asyncio.sleep(0.05)


asyncio.run(main())
"""

TEST_BENCH_URL = "http://127.0.0.1:54321"


@pytest.fixture(autouse=True)
def isolate_system_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep real Desktop/SDK state files out of CLI discovery tests."""

    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_state_root",
        lambda: tmp_path / "system-runtime-state",
    )


def test_ensure_runtime_reuses_shared_instance_state_across_private_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "shared-instance"
    private_root = tmp_path / "desktop-private"
    state = RuntimeProcessState(
        pid=42,
        control_url="http://127.0.0.1:18767",
        external_url="ws://127.0.0.1:18765",
        started_at=1.0,
    )
    RuntimeStateStore(shared_root).write(state)
    monkeypatch.setattr(watcherobot_cli, "default_runtime_instance_root", lambda: shared_root)
    monkeypatch.setattr(watcherobot_cli, "system_runtime_instance_root", lambda: shared_root)
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_state_root",
        lambda: tmp_path / "sdk-private",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.8",
                "instance_group": "default",
                "instance_id": runtime_instance_id(shared_root),
                "external_url": "ws://127.0.0.1:28765",
                "pid": 43,
                "started_at": 2.0,
            },
            "application": {"state": "not_selected"},
        },
    )
    monkeypatch.setattr(
        watcherobot_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("a shared Daemon must not start a second process"),
    )

    discovered, reused = watcherobot_cli.ensure_runtime(state_root=private_root)

    assert reused is True
    assert discovered == RuntimeProcessState(
        pid=43,
        control_url=state.control_url,
        external_url="ws://127.0.0.1:28765",
        started_at=2.0,
    )


def test_ensure_runtime_reuses_environment_isolated_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_root = tmp_path / "isolated-instance"
    state = RuntimeProcessState(
        pid=55,
        control_url="http://127.0.0.1:28767",
        external_url="ws://127.0.0.1:28765",
        started_at=3.0,
    )
    RuntimeStateStore(isolated_root).write(state)
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: isolated_root
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "system-instance",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.8",
                "instance_group": "isolated",
                "instance_id": runtime_instance_id(isolated_root),
                "external_url": state.external_url,
                "pid": state.pid,
                "started_at": state.started_at,
            },
            "application": {"state": "not_selected"},
        },
    )
    monkeypatch.setattr(
        watcherobot_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("the isolated Daemon must be reused"),
    )

    discovered, reused = watcherobot_cli.ensure_runtime(state_root=tmp_path / "data")

    assert reused is True
    assert discovered == state


def test_live_runtime_state_does_not_cross_reuse_isolated_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_root = tmp_path / "isolated-a"
    other_root = tmp_path / "isolated-b"
    RuntimeStateStore(requested_root).write(
        RuntimeProcessState(
            pid=55,
            control_url="http://127.0.0.1:28767",
            external_url="ws://127.0.0.1:28765",
            started_at=3.0,
        )
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: requested_root
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "system-instance",
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.8",
                "instance_group": "isolated",
                "instance_id": runtime_instance_id(other_root),
                "external_url": "ws://127.0.0.1:28765",
                "pid": 55,
                "started_at": 3.0,
            },
            "application": {"state": "not_selected"},
        },
    )

    assert watcherobot_cli._live_runtime_state(tmp_path / "data") is None


def test_live_runtime_state_validates_identity_from_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "shared-instance"
    RuntimeStateStore(shared_root).write(
        RuntimeProcessState(
            pid=42,
            control_url="http://127.0.0.1:18767",
            external_url="ws://127.0.0.1:18765",
            started_at=1.0,
        )
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "system_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk"
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.8",
                "instance_group": "isolated",
                "instance_id": runtime_instance_id(shared_root),
                "external_url": "ws://127.0.0.1:8765",
                "pid": 123,
                "started_at": 42.0,
            },
            "application": {"state": "not_selected"},
        },
    )

    assert watcherobot_cli._live_runtime_state(tmp_path / "private") is None


def test_live_runtime_state_discovers_default_group_by_control_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk")
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "18767")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "18765")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda base_url, path, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.6",
                "instance_group": "default",
                "instance_id": runtime_instance_id(tmp_path / "shared"),
                "external_url": "ws://127.0.0.1:28765",
                "pid": 123,
                "started_at": 42.0,
            },
            "application": {"state": "not_selected"},
        }
        if (base_url, path) == ("http://127.0.0.1:18767", "/daemon/status")
        else pytest.fail("unexpected Daemon endpoint"),
    )

    state = watcherobot_cli._live_runtime_state(tmp_path / "desktop-private")

    assert state is not None
    assert state.pid == 123
    assert state.control_url == "http://127.0.0.1:18767"
    assert state.external_url == "ws://127.0.0.1:28765"
    assert state.started_at == 42.0


def test_live_runtime_state_rejects_other_instance_on_fixed_control_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "sdk_version": "0.1.8",
                "instance_group": "isolated",
                "instance_id": runtime_instance_id(tmp_path / "shared"),
                "external_url": "ws://127.0.0.1:8765",
                "pid": 123,
                "started_at": 42.0,
            },
            "application": {"state": "not_selected"},
        },
    )

    with pytest.raises(watcherobot_cli.CliError, match="different WatcheRobot Daemon"):
        watcherobot_cli._live_runtime_state(tmp_path / "desktop-private")


def test_live_runtime_state_rejects_legacy_daemon_without_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: {
            "runtime": {
                "control_protocol": 2,
                "sdk_version": "0.1.7",
            },
            "application": {"state": "not_selected"},
        },
    )

    with pytest.raises(watcherobot_cli.CliError, match="Stop or restart"):
        watcherobot_cli._live_runtime_state(tmp_path / "desktop-private")


def test_legacy_daemon_blocks_status_start_and_stop_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "shared"
    state_root = tmp_path / "state"
    state = RuntimeProcessState(
        pid=42,
        control_url="http://127.0.0.1:18767",
        external_url="ws://127.0.0.1:18765",
        started_at=1.0,
    )
    RuntimeStateStore(state_root).write(state)
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "system_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_state_root", lambda: state_root
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda _base_url, _path, *, method="GET", **_kwargs: (
            pytest.fail("an incompatible Daemon must not receive a stop request")
            if method == "POST"
            else {
                "runtime": {
                    "control_protocol": 2,
                    "sdk_version": "0.1.7",
                },
                "application": {"state": "not_selected"},
            }
        ),
    )
    monkeypatch.setattr(
        watcherobot_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail(
            "an incompatible Daemon must block a second process"
        ),
    )

    for operation in (
        watcherobot_cli.runtime_status,
        lambda: watcherobot_cli.ensure_runtime(state_root=state_root),
        watcherobot_cli.stop_runtime,
    ):
        with pytest.raises(watcherobot_cli.CliError, match="runtime identity"):
            operation()

    assert RuntimeStateStore(state_root).read() == state


def test_stop_runtime_waits_for_owned_state_and_control_endpoint_to_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = RuntimeProcessState(
        pid=42,
        control_url="http://127.0.0.1:18767",
        external_url="ws://127.0.0.1:18765",
        started_at=1.0,
    )
    instance_root = tmp_path / "instance"
    store = RuntimeStateStore(instance_root)
    store.write(state)
    requests: list[tuple[str, str]] = []
    checks = iter([True, False])

    monkeypatch.setattr(watcherobot_cli, "_live_runtime_state", lambda: state)
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda base_url, path, **_kwargs: requests.append((base_url, path)) or {},
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: instance_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "_local_tcp_port_is_open", lambda _port: next(checks)
    )
    monkeypatch.setattr(watcherobot_cli.time, "sleep", lambda _seconds: store.remove())

    watcherobot_cli.stop_runtime()

    assert requests == [(state.control_url, "/daemon/stop")]
    assert store.read() is None


def test_stop_runtime_does_not_remove_unverified_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_root = tmp_path / "instance"
    foreign = RuntimeProcessState(
        pid=43,
        control_url="http://127.0.0.1:18767",
        external_url="ws://127.0.0.1:18765",
        started_at=2.0,
    )
    RuntimeStateStore(instance_root).write(foreign)
    monkeypatch.setattr(watcherobot_cli, "_live_runtime_state", lambda: None)
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: instance_root
    )

    watcherobot_cli.stop_runtime()

    assert RuntimeStateStore(instance_root).read() == foreign


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sdk_version", None),
        ("instance_group", "unknown"),
        ("instance_id", "sha256:short"),
        ("external_url", "http://127.0.0.1:8765"),
        ("pid", True),
        ("started_at", float("nan")),
    ],
)
def test_runtime_state_rejects_incomplete_or_invalid_identity_field(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    instance_root = tmp_path / "shared"
    runtime: dict[str, object] = {
        "sdk_version": "0.1.8",
        "instance_group": "default",
        "instance_id": runtime_instance_id(instance_root),
        "external_url": "ws://127.0.0.1:8765",
        "pid": 123,
        "started_at": 42.0,
    }
    runtime[field] = value

    with pytest.raises(watcherobot_cli.CliError, match="runtime identity"):
        watcherobot_cli._runtime_state_from_status(
            "http://127.0.0.1:8767",
            {"runtime": runtime},
            expected_group="default",
            expected_instance_id=runtime_instance_id(instance_root),
            allow_other_instance=False,
        )


@pytest.mark.parametrize(
    "status",
    [
        {"application": {"state": "not_selected"}},
        {"runtime": {"instance_group": "default"}},
        {"runtime": {"sdk_version": "0.1.8", "instance_group": "default"}},
    ],
)
def test_live_runtime_state_rejects_daemon_without_complete_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, object],
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk")
    monkeypatch.setattr(watcherobot_cli, "_request_json", lambda *_args, **_kwargs: status)

    with pytest.raises(watcherobot_cli.CliError, match="Stop or restart"):
        watcherobot_cli._live_runtime_state(tmp_path / "desktop-private")


@pytest.mark.parametrize("value", ["invalid", "-1", "65536"])
def test_live_runtime_state_rejects_invalid_control_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk"
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", value)

    with pytest.raises(
        watcherobot_cli.CliError,
        match="WATCHER_RUNTIME_CONTROL_PORT must be 0 or an integer",
    ):
        watcherobot_cli._live_runtime_state(tmp_path / "desktop-private")


def test_live_runtime_state_skips_fixed_endpoint_for_ephemeral_control_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watcherobot_cli,
        "default_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli,
        "system_runtime_instance_root",
        lambda: tmp_path / "shared",
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "sdk"
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: pytest.fail("port 0 has no fixed discovery endpoint"),
    )

    assert watcherobot_cli._live_runtime_state(tmp_path / "desktop-private") is None


def test_occupied_unrecognized_control_port_blocks_a_second_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "shared"
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "system_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(
        watcherobot_cli, "default_runtime_state_root", lambda: tmp_path / "state"
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "18767")
    monkeypatch.setattr(
        watcherobot_cli,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            watcherobot_cli.CliError("invalid HTTP response")
        ),
    )
    monkeypatch.setattr(watcherobot_cli, "_local_tcp_port_is_open", lambda _port: True)
    monkeypatch.setattr(
        watcherobot_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("an occupied port must block startup"),
    )

    with pytest.raises(watcherobot_cli.CliError, match="Refusing to start"):
        watcherobot_cli.ensure_runtime(state_root=tmp_path / "state")

URL_APPLICATION = f"""
import asyncio

from watcherobot.application import ApplicationContext


async def main():
    async with ApplicationContext.from_environment() as app:
        app.logger.info("SDK 测试网页：{TEST_BENCH_URL}")
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
) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    python = scripts_dir / "python.exe"
    pythonw = scripts_dir / "pythonw.exe"
    python.write_bytes(b"python redirector")
    pythonw.write_bytes(b"pythonw redirector")
    assert (
        watcherobot_cli._background_python_executable(
            python,
            is_windows=True,
        )
        == pythonw.resolve()
    )


def test_posix_background_daemon_preserves_virtualenv_launcher(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX virtualenv symlink semantics")
    runtime = tmp_path / "python-runtime"
    runtime.write_bytes(b"python runtime")
    scripts_dir = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    python = scripts_dir / "python"
    python.symlink_to(runtime)

    selected = watcherobot_cli._background_python_executable(
        python,
        is_windows=False,
    )

    assert selected == python
    assert selected.resolve() == runtime


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
        run_output = capsys.readouterr().out
        assert f"Running Application: {application_dir.resolve()}" in run_output
        assert "Press Ctrl+C to stop." in run_output
        assert "Application stdout:" in run_output
        assert "managed application entered" in run_output
        assert "Application finished: ended" in run_output
        assert main(["daemon", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["running"] is True
        assert status["application"]["current_app"] == "cli_test_app"
        assert status["application"]["state"] == "ended"
    finally:
        main(["daemon", "stop"])
        capsys.readouterr()


def test_cli_echoes_test_bench_url_from_application_startup_log(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    state_root = tmp_path / "runtime"
    application_dir = tmp_path / "application"
    _write_application(application_dir)
    application_dir.joinpath("app.py").write_text(
        URL_APPLICATION,
        encoding="utf-8",
    )
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(state_root))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_EXTERNAL_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PAIRING_PORT", "0")
    monkeypatch.setenv("WATCHER_RUNTIME_PREVIEW_UDP_PORT", "0")

    try:
        assert main(["app", "run", str(application_dir)]) == 0
        run_output = capsys.readouterr().out
        assert f"SDK 测试网页：{TEST_BENCH_URL}" in run_output
    finally:
        main(["daemon", "stop"])
        capsys.readouterr()


def test_application_log_echo_waits_for_complete_json_line(
    tmp_path,
    capsys,
) -> None:
    log_path = tmp_path / "application.jsonl"
    message = "SDK 测试网页：http://127.0.0.1:54321"
    encoded = json.dumps(
        {"stream": "stdout", "message": message},
        ensure_ascii=False,
    ).encode("utf-8")
    split_at = len(encoded) // 2
    log_path.write_bytes(encoded[:split_at])

    offset = watcherobot_cli._print_application_logs(log_path, after_offset=0)

    assert offset == 0
    assert capsys.readouterr().out == ""

    with log_path.open("ab") as log_file:
        log_file.write(encoded[split_at:] + b"\n")

    offset = watcherobot_cli._print_application_logs(
        log_path,
        after_offset=offset,
    )

    assert offset == len(encoded) + 1
    assert message in capsys.readouterr().out


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
    requests: list[
        tuple[str, str, str, dict[str, object] | None, dict[str, object]]
    ] = []

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

    def fake_request_json(base_url, path, *, method="GET", payload=None, **kwargs):
        requests.append((base_url, path, method, payload, kwargs))
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
        {},
    )
    assert requests[1] == (
        "http://isolated-runtime",
        "/daemon/application/start",
        "POST",
        None,
        {"timeout": 90.0},
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
