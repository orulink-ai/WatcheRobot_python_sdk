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
