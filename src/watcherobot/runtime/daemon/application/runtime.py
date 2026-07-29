"""Application subprocess supervision for the single runtime session."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psutil

from watcherobot.runtime.daemon.application.bridge import (
    LocalWebSocketApplicationBridge,
)
from watcherobot.runtime.daemon.application.logging import ApplicationLogService
from watcherobot.runtime.daemon.application.manifest import ApplicationManifest
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationRun,
    ApplicationSessionRegistry,
    ApplicationState,
    SessionOccupiedError,
)


class ApplicationRuntimeError(RuntimeError):
    """Base error for Application process supervision."""


class ApplicationStartError(ApplicationRuntimeError):
    """Raised when the current Application doesn't become ready."""


DEFAULT_APPLICATION_STARTUP_TIMEOUT = 30.0


def resolve_application_command(
    *,
    application_dir: Path,
    python_executable: str = sys.executable,
    frozen: bool | None = None,
) -> tuple[str, ...]:
    """Resolve the child command for source and frozen shared runtimes."""

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return (python_executable, "--application")
    return (python_executable, str(Path(application_dir).resolve() / "app.py"))


class ApplicationRuntimeManager:
    """Start, observe, and stop the selected Application process tree."""

    def __init__(
        self,
        *,
        application_dir: Path,
        current_app: str,
        python_executable: str = sys.executable,
        startup_timeout: float = DEFAULT_APPLICATION_STARTUP_TIMEOUT,
        stop_timeout: float = 5.0,
        log_service: ApplicationLogService | None = None,
    ) -> None:
        self._application_dir = Path(application_dir).resolve()
        self._python_executable = python_executable
        self._application_command = resolve_application_command(
            application_dir=self._application_dir,
            python_executable=python_executable,
        )
        self._startup_timeout = startup_timeout
        self._stop_timeout = stop_timeout
        self._log_service = log_service
        self.registry = ApplicationSessionRegistry(current_app=current_app)
        self.bridge = LocalWebSocketApplicationBridge(
            registry=self.registry,
            on_channel_lost=self._on_channel_lost,
        )
        self.last_state = ApplicationState.NOT_RUNNING
        self.last_exit_code: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._closing = False
        self._log_tasks: list[asyncio.Task[None]] = []

    @property
    def process_id(self) -> int | None:
        process = self._process
        if process is None or process.returncode is not None:
            return None
        return process.pid

    def select_application(
        self,
        application_dir: Path,
    ) -> ApplicationManifest:
        """Select a validated Application while preserving the Runtime."""

        if self._process is not None or self.registry.active_run is not None:
            raise SessionOccupiedError(
                "Application cannot change while a process exists"
            )
        selected_dir = Path(application_dir).resolve()
        manifest = ApplicationManifest.load(selected_dir)
        self.registry.set_current_app(manifest.app_id)
        self._application_dir = selected_dir
        self._application_command = resolve_application_command(
            application_dir=selected_dir,
            python_executable=self._python_executable,
        )
        self.last_state = ApplicationState.NOT_RUNNING
        self.last_exit_code = None
        return manifest

    async def start(self) -> ApplicationRun:
        async with self._operation_lock:
            if self._process is not None or self.registry.active_run is not None:
                raise SessionOccupiedError("an Application process already exists")

            manifest = ApplicationManifest.load(self._application_dir)
            if manifest.app_id != self.registry.current_app:
                raise ApplicationStartError(
                    "Application manifest id does not match current app"
                )

            run = self.registry.begin_start()
            self.last_state = ApplicationState.STARTING
            self.last_exit_code = None
            await self.bridge.start()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self._application_command,
                    cwd=str(self._application_dir),
                    env=self._build_environment(run),
                    stdout=(
                        asyncio.subprocess.PIPE
                        if self._log_service is not None
                        else asyncio.subprocess.DEVNULL
                    ),
                    stderr=(
                        asyncio.subprocess.PIPE
                        if self._log_service is not None
                        else asyncio.subprocess.DEVNULL
                    ),
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if os.name == "nt"
                        else 0
                    ),
                )
            except Exception:
                await self.bridge.stop()
                self.registry.end_run(ApplicationState.ERROR)
                self.last_state = ApplicationState.ERROR
                raise
            self._start_log_readers(self._process, manifest.app_id)
            self._monitor_task = asyncio.create_task(
                self._monitor_process(self._process),
                name=f"application-monitor-{manifest.app_id}",
            )

        await self._wait_until_ready(run)
        return run

    async def stop(self) -> None:
        async with self._operation_lock:
            if self._process is None and self.registry.active_run is None:
                return
            self._closing = True
            process = self._process
            monitor_task = self._monitor_task
            try:
                if process is not None:
                    tracked_processes = await asyncio.to_thread(
                        _collect_process_tree,
                        process.pid,
                    )
                    await self.bridge.stop()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(process.wait()),
                            timeout=min(1.0, self._stop_timeout),
                        )
                    except TimeoutError:
                        pass
                    await asyncio.to_thread(
                        _terminate_process_ids,
                        tracked_processes,
                        self._stop_timeout,
                    )
                    await process.wait()
                    await self._wait_for_log_tasks()
                    self.last_exit_code = process.returncode
                else:
                    await self.bridge.stop()
                if self.registry.active_run is not None:
                    self.registry.end_run(ApplicationState.ENDED)
                self.last_state = ApplicationState.ENDED
                self._process = None
                self._monitor_task = None
            finally:
                self._closing = False

        if monitor_task is not None:
            await monitor_task

    def _build_environment(self, run: ApplicationRun) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "WATCHER_APP_ID": run.app_id,
                "WATCHER_APP_RUN_CREDENTIAL": run.credential,
                "WATCHER_APP_DESKTOP_WS_URL": self.bridge.channel_url(
                    ApplicationChannel.DESKTOP,
                    credential=run.credential,
                ),
                "WATCHER_APP_DEVICE_WS_URL": self.bridge.channel_url(
                    ApplicationChannel.DEVICE,
                    credential=run.credential,
                ),
            }
        )
        return environment

    async def _wait_until_ready(self, run: ApplicationRun) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._startup_timeout
        while loop.time() < deadline:
            if run.state is ApplicationState.RUNNING:
                self.last_state = ApplicationState.RUNNING
                return
            if self.registry.active_run is None:
                raise ApplicationStartError(
                    "Application exited before startup completed: "
                    f"code={self.last_exit_code}"
                )
            await asyncio.sleep(0.01)

        await self._abort_for_error()
        raise ApplicationStartError("Application startup timed out")

    async def _monitor_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        return_code = await process.wait()
        await self._wait_for_log_tasks()
        self.last_exit_code = return_code
        if not self._closing and self._process is process:
            await self._finalize_exited_process(
                process,
                return_code=return_code,
            )

    def _on_channel_lost(self, _channel: ApplicationChannel) -> None:
        if not self._closing:
            asyncio.create_task(
                self._handle_channel_lost(),
                name="application-channel-loss-cleanup",
            )

    async def _handle_channel_lost(self) -> None:
        process = self._process
        if process is None:
            await self._abort_for_error()
            return
        try:
            return_code = await asyncio.wait_for(
                asyncio.shield(process.wait()),
                timeout=0.5,
            )
        except TimeoutError:
            await self._abort_for_error()
            return
        await self._finalize_exited_process(
            process,
            return_code=return_code,
        )

    async def _finalize_exited_process(
        self,
        process: asyncio.subprocess.Process,
        *,
        return_code: int,
    ) -> None:
        async with self._operation_lock:
            if self._closing or self._process is not process:
                return
            self._closing = True
            try:
                await self._wait_for_log_tasks()
                self.last_exit_code = return_code
                await self.bridge.stop()
                final_state = (
                    ApplicationState.ENDED
                    if return_code == 0
                    else ApplicationState.ERROR
                )
                if self.registry.active_run is not None:
                    self.registry.end_run(final_state)
                self.last_state = final_state
                self._process = None
                self._monitor_task = None
            finally:
                self._closing = False

    async def _abort_for_error(self) -> None:
        async with self._operation_lock:
            if self._closing:
                return
            self._closing = True
            process = self._process
            monitor_task = self._monitor_task
            try:
                if process is not None and process.returncode is None:
                    await asyncio.to_thread(
                        _terminate_process_tree,
                        process.pid,
                        self._stop_timeout,
                    )
                    await process.wait()
                    await self._wait_for_log_tasks()
                    self.last_exit_code = process.returncode
                await self.bridge.stop()
                if self.registry.active_run is not None:
                    self.registry.end_run(ApplicationState.ERROR)
                self.last_state = ApplicationState.ERROR
                self._process = None
                self._monitor_task = None
            finally:
                self._closing = False

        current_task = asyncio.current_task()
        if monitor_task is not None and monitor_task is not current_task:
            await monitor_task

    def _start_log_readers(
        self,
        process: asyncio.subprocess.Process,
        app_id: str,
    ) -> None:
        if self._log_service is None:
            return
        for stream_name, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            if stream is None:
                continue
            self._log_tasks.append(
                asyncio.create_task(
                    self._read_log_stream(
                        app_id=app_id,
                        stream_name=stream_name,
                        stream=stream,
                    ),
                    name=f"application-{stream_name}-{app_id}",
                )
            )

    async def _read_log_stream(
        self,
        *,
        app_id: str,
        stream_name: str,
        stream: asyncio.StreamReader,
    ) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            assert self._log_service is not None
            await self._log_service.record(
                app_id=app_id,
                stream=stream_name,
                message=line.decode("utf-8", errors="replace").rstrip("\r\n"),
            )

    async def _wait_for_log_tasks(self) -> None:
        tasks = self._log_tasks
        self._log_tasks = []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _terminate_process_tree(pid: int, timeout: float) -> None:
    _terminate_process_ids(_collect_process_tree(pid), timeout)


def _collect_process_tree(pid: int) -> list[int]:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []

    return [
        *(process.pid for process in parent.children(recursive=True)),
        parent.pid,
    ]


def _terminate_process_ids(pids: list[int], timeout: float) -> None:
    processes: list[psutil.Process] = []
    for pid in pids:
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    for process in reversed(processes):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue

    _gone, alive = psutil.wait_procs(processes, timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=timeout)
