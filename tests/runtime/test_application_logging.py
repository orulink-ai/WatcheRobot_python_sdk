from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from watcherobot.runtime.daemon.application.launcher import ApplicationLauncher
from watcherobot.runtime.daemon.application.logging import ApplicationLogService
from watcherobot.runtime.daemon.application.runtime import (
    ApplicationRuntimeManager,
    ApplicationStartError,
)


LOGGING_APPLICATION = """
import asyncio
import os
import sys

from websockets.asyncio.client import connect


async def main():
    print("application stdout line", flush=True)
    print("application stderr line", file=sys.stderr, flush=True)
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]),
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]),
    ):
        await asyncio.Event().wait()


asyncio.run(main())
"""

SILENT_EXIT_APPLICATION = "raise SystemExit(1)\n"


def _write_application(root: Path, source: str = LOGGING_APPLICATION) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "logging_app",
                "name": "Logging Application",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(source, encoding="utf-8")


def test_log_service_saves_before_forwarding_and_ignores_desktop_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        forwarded: list[str] = []

        async def failing_forwarder(frame: str) -> None:
            forwarded.append(frame)
            raise ConnectionError("desktop offline")

        service = ApplicationLogService(
            log_dir=tmp_path / "logs",
            desktop_forwarder=failing_forwarder,
        )

        await service.record(
            app_id="logging_app",
            stream="stdout",
            message="hello watcher",
        )

        records = [
            json.loads(line)
            for line in service.log_path("logging_app")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert records[0]["message"] == "hello watcher"
        assert records[0]["stream"] == "stdout"
        assert len(forwarded) == 1
        assert json.loads(forwarded[0])["type"] == "daemon.application.log"
        assert service.last_forward_error == "desktop offline"

    asyncio.run(scenario())


def test_runtime_captures_application_stdout_and_stderr(tmp_path: Path) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir)
        forwarded: list[str] = []
        service = ApplicationLogService(
            log_dir=tmp_path / "logs",
            desktop_forwarder=lambda frame: _append(forwarded, frame),
        )
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="logging_app",
            application_launcher=ApplicationLauncher(
                managed_app_root=Path(sys.executable).resolve().parent,
                bundled_resource_root=tmp_path / "resources",
            ),
            startup_timeout=3,
            stop_timeout=3,
            log_service=service,
        )
        manager.select_application(
            app_dir,
            launcher_kind="python",
            launcher_executable=Path(sys.executable).resolve(),
        )

        await manager.start()
        for _ in range(100):
            if len(forwarded) >= 2:
                break
            await asyncio.sleep(0.01)
        await manager.stop()

        records = [
            json.loads(line)
            for line in service.log_path("logging_app")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert {(record["stream"], record["message"]) for record in records} >= {
            ("stdout", "application stdout line"),
            ("stderr", "application stderr line"),
        }
        assert len(forwarded) >= 2
        assert manager.process_id is None

    asyncio.run(scenario())


def test_log_service_can_persist_lifecycle_without_forwarding(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        forwarded: list[str] = []
        service = ApplicationLogService(
            log_dir=tmp_path / "logs",
            desktop_forwarder=lambda frame: _append(forwarded, frame),
        )

        await service.record(
            app_id="logging_app",
            stream="lifecycle",
            message="Application launch requested",
            forward_to_desktop=False,
        )

        assert service.log_path("logging_app").is_file()
        assert forwarded == []

    asyncio.run(scenario())


def test_runtime_writes_lifecycle_log_when_application_exits_silently(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, SILENT_EXIT_APPLICATION)
        service = ApplicationLogService(
            log_dir=tmp_path / "logs",
            desktop_forwarder=lambda _frame: None,
        )
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="logging_app",
            application_launcher=ApplicationLauncher(
                managed_app_root=Path(sys.executable).resolve().parent,
                bundled_resource_root=tmp_path / "resources",
            ),
            startup_timeout=3,
            stop_timeout=3,
            log_service=service,
        )
        manager.select_application(
            app_dir,
            launcher_kind="python",
            launcher_executable=Path(sys.executable).resolve(),
        )

        try:
            await manager.start()
        except ApplicationStartError:
            pass

        records = [
            json.loads(line)
            for line in service.log_path("logging_app")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        lifecycle_messages = [
            record["message"]
            for record in records
            if record["stream"] == "lifecycle"
        ]
        assert lifecycle_messages[0] == "Application launch requested"
        assert any(message.endswith("code=1") for message in lifecycle_messages)

    asyncio.run(scenario())


async def _append(target: list[str], frame: str) -> None:
    target.append(frame)
