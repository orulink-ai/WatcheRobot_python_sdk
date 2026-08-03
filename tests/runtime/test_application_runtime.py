from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from watcherobot.runtime.daemon.application.runtime import (
    DEFAULT_APPLICATION_STARTUP_TIMEOUT,
    ApplicationRuntimeManager,
    ApplicationStartError,
    resolve_application_command,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationNotSelectedError,
    ApplicationState,
    SessionOccupiedError,
)
from watcherobot.runtime.frozen_entry import main as frozen_runtime_main


CONNECTED_APP = """
import asyncio
import os

from websockets.asyncio.client import connect


async def main():
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]),
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]),
    ):
        await asyncio.Event().wait()


asyncio.run(main())
"""


COMPLETING_APP = """
import asyncio
import os

from websockets.asyncio.client import connect


async def main():
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]),
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]),
    ):
        await asyncio.sleep(0.05)


asyncio.run(main())
"""


CHILD_PROCESS_APP = """
import asyncio
import os
from pathlib import Path
import subprocess
import sys

from websockets.asyncio.client import connect


async def main():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
    )
    Path(os.environ["CHILD_PID_FILE"]).write_text(
        str(child.pid),
        encoding="utf-8",
    )
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]),
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]),
    ):
        await asyncio.Event().wait()


asyncio.run(main())
"""


def _write_application(
    root: Path,
    source: str,
    *,
    app_id: str = "test_app",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": app_id,
                "name": "Test Application",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(source, encoding="utf-8")


def test_runtime_starts_only_current_app_and_stops_cleanly(tmp_path: Path) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, CONNECTED_APP)
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            python_executable=sys.executable,
            startup_timeout=3,
            stop_timeout=3,
        )

        run = await manager.start()

        assert run.app_id == "test_app"
        assert run.state is ApplicationState.RUNNING
        assert manager.process_id is not None
        with pytest.raises(SessionOccupiedError):
            await manager.start()

        await manager.stop()

        assert manager.process_id is None
        assert manager.last_state is ApplicationState.ENDED
        assert manager.registry.active_run is None

    asyncio.run(scenario())


def test_runtime_releases_session_when_application_fails_to_start(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, "raise SystemExit(7)")
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            python_executable=sys.executable,
            startup_timeout=3,
            stop_timeout=3,
        )

        with pytest.raises(ApplicationStartError):
            await manager.start()

        assert manager.last_state is ApplicationState.ERROR
        assert manager.last_exit_code == 7
        assert manager.process_id is None
        assert manager.registry.active_run is None

    asyncio.run(scenario())


def test_runtime_marks_a_normally_completed_application_as_ended(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, COMPLETING_APP)
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            python_executable=sys.executable,
            startup_timeout=3,
            stop_timeout=3,
        )

        await manager.start()
        for _ in range(200):
            if manager.last_state in {
                ApplicationState.ENDED,
                ApplicationState.ERROR,
            }:
                break
            await asyncio.sleep(0.01)

        assert manager.last_state is ApplicationState.ENDED
        assert manager.last_exit_code == 0
        assert manager.registry.active_run is None

    asyncio.run(scenario())


def test_runtime_stop_cleans_application_process_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        child_pid_file = tmp_path / "child.pid"
        _write_application(app_dir, CHILD_PROCESS_APP)
        monkeypatch.setenv("CHILD_PID_FILE", str(child_pid_file))
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            python_executable=sys.executable,
            startup_timeout=3,
            stop_timeout=3,
        )

        await manager.start()
        for _ in range(100):
            if child_pid_file.exists():
                break
            await asyncio.sleep(0.01)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert psutil.pid_exists(child_pid)

        await manager.stop()

        for _ in range(100):
            if not psutil.pid_exists(child_pid):
                break
            await asyncio.sleep(0.01)
        assert not psutil.pid_exists(child_pid)

    asyncio.run(scenario())


def test_default_runtime_allows_cold_application_startup() -> None:
    assert DEFAULT_APPLICATION_STARTUP_TIMEOUT == 30.0


def test_source_runtime_launches_application_with_shared_python(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "application"

    assert resolve_application_command(
        application_dir=app_dir,
        python_executable="python-shared",
        frozen=False,
    ) == ("python-shared", str(app_dir.resolve() / "app.py"))


def test_frozen_runtime_launches_the_selected_application_directory(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "application"

    assert resolve_application_command(
        application_dir=app_dir,
        python_executable="watcher-runtime.exe",
        frozen=True,
    ) == (
        "watcher-runtime.exe",
        "--application",
        str(app_dir.resolve()),
    )


def test_runtime_represents_an_unselected_application_explicitly(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = ApplicationRuntimeManager(
            application_dir=tmp_path / "unselected",
            current_app=None,
            python_executable=sys.executable,
        )

        assert manager.registry.current_app is None
        assert manager.last_state is ApplicationState.NOT_SELECTED
        with pytest.raises(ApplicationNotSelectedError):
            await manager.start()

    asyncio.run(scenario())


def test_frozen_entry_executes_a_validated_application_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_dir = tmp_path / "application"
    marker = tmp_path / "application-ran.txt"
    _write_application(
        app_dir,
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['APPLICATION_MARKER']).write_text('ok', encoding='utf-8')\n",
    )
    monkeypatch.setenv("APPLICATION_MARKER", str(marker))

    assert frozen_runtime_main(["--application", str(app_dir)]) == 0
    assert marker.read_text(encoding="utf-8") == "ok"


def test_runtime_can_select_another_application_without_restarting_itself(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        first_dir = tmp_path / "first"
        second_dir = tmp_path / "second"
        _write_application(first_dir, CONNECTED_APP, app_id="first_app")
        _write_application(second_dir, CONNECTED_APP, app_id="second_app")
        manager = ApplicationRuntimeManager(
            application_dir=first_dir,
            current_app="first_app",
            python_executable=sys.executable,
            startup_timeout=3,
            stop_timeout=3,
        )

        selected = manager.select_application(second_dir)

        assert selected.app_id == "second_app"
        assert manager.registry.current_app == "second_app"
        run = await manager.start()
        assert run.app_id == "second_app"
        await manager.stop()

    asyncio.run(scenario())
