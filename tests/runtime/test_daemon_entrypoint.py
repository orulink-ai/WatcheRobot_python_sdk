from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from watcherobot.runtime.daemon import __main__ as daemon_entrypoint
from watcherobot.runtime.daemon.runtime import DaemonRuntime


def _write_application(root: Path) -> Path:
    root.mkdir(parents=True)
    root.joinpath("app.json").write_text(
        (
            '{"schema_version":1,"id":"watcher_default",'
            '"name":"Default","version":"1.0.0",'
            '"requires_watcherobot":">=0.1.0a4,<0.2","dependencies":[]}'
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("pass\n", encoding="utf-8")
    return root.resolve()


def test_runtime_publishes_shared_and_legacy_coordination_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_events: list[tuple[str, Path]] = []
    store_events: list[tuple[str, Path, object | None]] = []

    class FakeLock:
        def __init__(self, path: Path) -> None:
            self.path = path

        def acquire(self) -> None:
            lock_events.append(("acquire", self.path))

        def release(self) -> None:
            lock_events.append(("release", self.path))

    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def write(self, state: object) -> None:
            store_events.append(("write", self.root, state))

        def remove(self) -> None:
            store_events.append(("remove", self.root, None))

    class FakeRuntime:
        def __init__(self, **_kwargs: object) -> None:
            self.control_server = type("Control", (), {"base_url": "http://127.0.0.1:18767"})()
            self.external_server = type("External", (), {"url": "ws://127.0.0.1:18765"})()

        async def start(self) -> None:
            return None

        async def wait_for_shutdown(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    private_root = (tmp_path / "desktop-private").resolve()
    shared_root = (tmp_path / "shared-instance").resolve()
    legacy_root = (tmp_path / "sdk-default").resolve()
    monkeypatch.setattr(daemon_entrypoint, "RuntimeInstanceLock", FakeLock)
    monkeypatch.setattr(daemon_entrypoint, "RuntimeStateStore", FakeStore)
    monkeypatch.setattr(daemon_entrypoint, "DaemonRuntime", FakeRuntime)
    monkeypatch.setattr(
        daemon_entrypoint, "default_runtime_instance_root", lambda: shared_root
    )
    monkeypatch.setattr(daemon_entrypoint, "default_runtime_state_root", lambda: legacy_root)
    args = daemon_entrypoint.build_parser().parse_args(
        ["--state-root", str(private_root), "--instance-root", str(shared_root)]
    )

    assert asyncio.run(daemon_entrypoint.run_runtime(args)) == 0

    acquired = [path for event, path in lock_events if event == "acquire"]
    assert acquired == [
        shared_root / "runtime.lock",
        legacy_root / "runtime.lock",
        private_root / "runtime.lock",
    ]
    writes = [(root, state) for event, root, state in store_events if event == "write"]
    assert [root for root, _state in writes] == [shared_root, legacy_root, private_root]
    assert {state.control_url for _root, state in writes} == {"http://127.0.0.1:18767"}


def test_runtime_explicit_instance_root_stays_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_paths: list[Path] = []

    class FakeLock:
        def __init__(self, path: Path) -> None:
            self.path = path

        def acquire(self) -> None:
            lock_paths.append(self.path)

        def release(self) -> None:
            return None

    class FakeRuntime:
        def __init__(self, **_kwargs: object) -> None:
            self.control_server = type("Control", (), {"base_url": "http://127.0.0.1:18767"})()
            self.external_server = type("External", (), {"url": "ws://127.0.0.1:18765"})()

        async def start(self) -> None:
            return None

        async def wait_for_shutdown(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    default_root = (tmp_path / "default-instance").resolve()
    isolated_root = (tmp_path / "isolated-instance").resolve()
    monkeypatch.setattr(daemon_entrypoint, "RuntimeInstanceLock", FakeLock)
    monkeypatch.setattr(daemon_entrypoint, "DaemonRuntime", FakeRuntime)
    monkeypatch.setattr(
        daemon_entrypoint, "default_runtime_instance_root", lambda: default_root
    )
    args = daemon_entrypoint.build_parser().parse_args(
        [
            "--state-root",
            str(tmp_path / "isolated-state"),
            "--instance-root",
            str(isolated_root),
        ]
    )

    assert asyncio.run(daemon_entrypoint.run_runtime(args)) == 0
    assert lock_paths == [isolated_root / "runtime.lock"]


@pytest.mark.parametrize(
    "option,value",
    [
        ("--source-default-application-root", "application"),
        ("--source-default-launcher", "python"),
    ],
)
def test_cli_requires_source_default_options_as_a_pair(
    option: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="2"):
        daemon_entrypoint.main([option, value])

    assert (
        "--source-default-application-root and --source-default-launcher "
        "must be provided together"
        in capsys.readouterr().err
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenvs use Python symlinks")
def test_cli_preserves_source_default_launcher_through_application_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_root = _write_application(
        tmp_path / "workspace" / "WatcheRobot_server"
    )
    base_python = tmp_path / "homebrew" / "bin" / "python3.14"
    base_python.parent.mkdir(parents=True)
    base_python.write_bytes(b"python")
    base_python.chmod(0o755)
    virtualenv_python = tmp_path / "workspace" / ".runtime" / "venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(base_python)
    captured: dict[str, DaemonRuntime] = {}
    runtime_type = daemon_entrypoint.DaemonRuntime

    def runtime_factory(**kwargs: Any) -> DaemonRuntime:
        runtime = runtime_type(**kwargs)
        captured["runtime"] = runtime
        return runtime

    async def no_op(_self: DaemonRuntime) -> None:
        return None

    async def start_without_network(runtime: DaemonRuntime) -> None:
        runtime.control_server._bound_port = 18767
        runtime.external_server._bound_port = 18765

    monkeypatch.setattr(daemon_entrypoint, "DaemonRuntime", runtime_factory)
    monkeypatch.setattr(runtime_type, "start", start_without_network)
    monkeypatch.setattr(runtime_type, "stop", no_op)
    monkeypatch.setattr(runtime_type, "wait_for_shutdown", no_op)

    args = daemon_entrypoint.build_parser().parse_args(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--source-default-application-root",
            str(application_root),
            "--source-default-launcher",
            str(virtualenv_python),
        ]
    )

    assert asyncio.run(daemon_entrypoint.run_runtime(args)) == 0
    runtime = captured["runtime"]
    launcher = runtime.application._application_launcher
    spec = launcher.build_spec(
        application_dir=application_root,
        kind="python",
        executable=virtualenv_python,
    )

    assert spec.executable == virtualenv_python
    assert spec.command == (virtualenv_python, application_root / "app.py")
