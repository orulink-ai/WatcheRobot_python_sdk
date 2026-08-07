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
)
from watcherobot.runtime.daemon.application import runtime as application_runtime
from watcherobot.runtime.daemon.application.launcher import (
    ApplicationLauncher,
    ApplicationLauncherKind,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationNotSelectedError,
    ApplicationState,
    SessionOccupiedError,
)


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


ENVIRONMENT_APP = """
import asyncio
import json
import os
from pathlib import Path

from websockets.asyncio.client import connect


async def main():
    Path(os.environ["ENVIRONMENT_FILE"]).write_text(
        json.dumps(
            {
                name: os.environ.get(name)
                for name in (
                    "PYTHONPATH",
                    "PYTHONHOME",
                    "VIRTUAL_ENV",
                    "PYTHONNOUSERSITE",
                    "PYTHONUNBUFFERED",
                )
            }
        ),
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


def _build_selected_manager(
    application_dir: Path,
    *,
    app_id: str,
    startup_timeout: float = 3,
    stop_timeout: float = 3,
) -> ApplicationRuntimeManager:
    python_executable = Path(sys.executable).resolve()
    manager = ApplicationRuntimeManager(
        application_dir=application_dir,
        current_app=app_id,
        application_launcher=ApplicationLauncher(
            managed_app_root=python_executable.parent,
            bundled_resource_root=application_dir.parent / "resources",
        ),
        startup_timeout=startup_timeout,
        stop_timeout=stop_timeout,
    )
    manager.select_application(
        application_dir,
        launcher_kind="python",
        launcher_executable=python_executable,
    )
    return manager


def test_windows_application_launch_hides_the_console_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(application_runtime.os, "name", "nt")
    monkeypatch.setattr(
        application_runtime.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0x200,
        raising=False,
    )
    monkeypatch.setattr(
        application_runtime.subprocess,
        "DETACHED_PROCESS",
        0x8,
        raising=False,
    )
    monkeypatch.setattr(
        application_runtime.subprocess,
        "CREATE_NO_WINDOW",
        0x8000000,
        raising=False,
    )

    assert application_runtime._application_creation_flags() == 0x8000208


def test_runtime_starts_only_current_app_and_stops_cleanly(tmp_path: Path) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, CONNECTED_APP)
        manager = _build_selected_manager(app_dir, app_id="test_app")

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
        manager = _build_selected_manager(app_dir, app_id="test_app")

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
        manager = _build_selected_manager(app_dir, app_id="test_app")

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
        manager = _build_selected_manager(app_dir, app_id="test_app")

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
    assert DEFAULT_APPLICATION_STARTUP_TIMEOUT >= 90.0


def test_runtime_requires_a_controlled_launch_spec_before_starting(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_application(app_dir, CONNECTED_APP)
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            application_launcher=ApplicationLauncher(
                managed_app_root=tmp_path / "application-store",
                bundled_resource_root=tmp_path / "resources",
            ),
        )

        with pytest.raises(ApplicationStartError, match="launch specification"):
            await manager.start()

    asyncio.run(scenario())


def test_runtime_represents_an_unselected_application_explicitly(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = ApplicationRuntimeManager(
            application_dir=tmp_path / "unselected",
            current_app=None,
            application_launcher=ApplicationLauncher(
                managed_app_root=tmp_path / "application-store",
                bundled_resource_root=tmp_path / "bundled-resources",
            ),
        )

        assert manager.registry.current_app is None
        assert manager.last_state is ApplicationState.NOT_SELECTED
        with pytest.raises(ApplicationNotSelectedError):
            await manager.start()

    asyncio.run(scenario())

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
            application_launcher=ApplicationLauncher(
                managed_app_root=Path(sys.executable).resolve().parent,
                bundled_resource_root=tmp_path / "resources",
            ),
            startup_timeout=3,
            stop_timeout=3,
        )

        selected = manager.select_application(
            second_dir,
            launcher_kind="python",
            launcher_executable=Path(sys.executable).resolve(),
        )

        assert selected.app_id == "second_app"
        assert manager.registry.current_app == "second_app"
        run = await manager.start()
        assert run.app_id == "second_app"
        await manager.stop()

    asyncio.run(scenario())


def test_runtime_selects_directory_and_controlled_launcher_atomically(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _write_application(first_dir, CONNECTED_APP, app_id="first_app")
    _write_application(second_dir, CONNECTED_APP, app_id="second_app")
    managed_root = tmp_path / "application-store"
    python_executable = managed_root / "second" / (
        "python.exe" if sys.platform == "win32" else "python"
    )
    python_executable.parent.mkdir(parents=True)
    python_executable.write_bytes(b"controlled launcher")
    if sys.platform != "win32":
        python_executable.chmod(0o755)
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=tmp_path / "resources",
    )
    manager = ApplicationRuntimeManager(
        application_dir=first_dir,
        current_app="first_app",
        application_launcher=launcher,
    )

    selected = manager.select_application(
        second_dir,
        launcher_kind="python",
        launcher_executable=python_executable,
    )

    assert selected.app_id == "second_app"
    assert manager.registry.current_app == "second_app"
    assert manager.launch_spec is not None
    assert manager.launch_spec.kind is ApplicationLauncherKind.PYTHON
    assert manager.launch_spec.command == (
        python_executable.resolve(),
        second_dir.resolve() / "app.py",
    )


def test_runtime_cleans_inherited_python_environment_for_selected_app(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        environment_file = tmp_path / "environment.json"
        _write_application(app_dir, ENVIRONMENT_APP)
        python_executable = Path(sys.executable).resolve()
        launcher = ApplicationLauncher(
            managed_app_root=python_executable.parent,
            bundled_resource_root=tmp_path / "resources",
        )
        manager = ApplicationRuntimeManager(
            application_dir=app_dir,
            current_app="test_app",
            application_launcher=launcher,
            startup_timeout=3,
            stop_timeout=3,
        )
        manager.select_application(
            app_dir,
            launcher_kind="python",
            launcher_executable=python_executable,
        )
        monkeypatch.setenv("PYTHONPATH", "polluted-python-path")
        monkeypatch.setenv("PYTHONHOME", "polluted-python-home")
        monkeypatch.setenv("VIRTUAL_ENV", "polluted-virtual-env")
        monkeypatch.setenv("ENVIRONMENT_FILE", str(environment_file))

        await manager.start()
        environment = json.loads(environment_file.read_text(encoding="utf-8"))
        await manager.stop()

        assert environment == {
            "PYTHONPATH": None,
            "PYTHONHOME": None,
            "VIRTUAL_ENV": None,
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }

    asyncio.run(scenario())


def test_running_application_cannot_switch_controlled_launcher(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        first_dir = tmp_path / "first"
        second_dir = tmp_path / "second"
        _write_application(first_dir, CONNECTED_APP, app_id="first_app")
        _write_application(second_dir, CONNECTED_APP, app_id="second_app")
        python_executable = Path(sys.executable).resolve()
        launcher = ApplicationLauncher(
            managed_app_root=python_executable.parent,
            bundled_resource_root=tmp_path / "resources",
        )
        manager = ApplicationRuntimeManager(
            application_dir=first_dir,
            current_app="first_app",
            application_launcher=launcher,
            startup_timeout=3,
            stop_timeout=3,
        )
        manager.select_application(
            first_dir,
            launcher_kind="python",
            launcher_executable=python_executable,
        )
        await manager.start()

        with pytest.raises(SessionOccupiedError):
            manager.select_application(
                second_dir,
                launcher_kind="python",
                launcher_executable=python_executable,
            )

        assert manager.registry.current_app == "first_app"
        assert manager.launch_spec is not None
        assert manager.launch_spec.application_dir == first_dir.resolve()
        await manager.stop()

    asyncio.run(scenario())
