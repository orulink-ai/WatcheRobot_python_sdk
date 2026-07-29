"""Single-Application runtime session and channel admission rules."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum


class ApplicationState(str, Enum):
    NOT_RUNNING = "not_running"
    STARTING = "starting"
    RUNNING = "running"
    ENDED = "ended"
    ERROR = "error"


class ApplicationChannel(str, Enum):
    DESKTOP = "desktop"
    DEVICE = "device"


class ApplicationSessionError(RuntimeError):
    """Base error for Application runtime-session operations."""


class SessionOccupiedError(ApplicationSessionError):
    """Raised when the single runtime or channel slot is already occupied."""


class InvalidRunCredentialError(ApplicationSessionError):
    """Raised when a channel doesn't belong to the current Application run."""


@dataclass
class ApplicationRun:
    app_id: str
    credential: str = field(repr=False)
    state: ApplicationState = ApplicationState.STARTING
    connected_channels: set[ApplicationChannel] = field(default_factory=set)


class ApplicationSessionRegistry:
    """Own the selected Application and its only allowed runtime session."""

    def __init__(self, *, current_app: str) -> None:
        normalized_app_id = str(current_app or "").strip()
        if not normalized_app_id:
            raise ValueError("current_app must not be empty")
        self._current_app = normalized_app_id
        self._active_run: ApplicationRun | None = None

    @property
    def current_app(self) -> str:
        return self._current_app

    @property
    def active_run(self) -> ApplicationRun | None:
        return self._active_run

    def set_current_app(self, app_id: str) -> None:
        if self._active_run is not None:
            raise SessionOccupiedError(
                "current app cannot change while an Application session exists"
            )
        normalized_app_id = str(app_id or "").strip()
        if not normalized_app_id:
            raise ValueError("app_id must not be empty")
        self._current_app = normalized_app_id

    def begin_start(self) -> ApplicationRun:
        if self._active_run is not None:
            raise SessionOccupiedError("an Application session already exists")
        run = ApplicationRun(
            app_id=self._current_app,
            credential=secrets.token_urlsafe(32),
        )
        self._active_run = run
        return run
    def attach_channel(
        self,
        channel: ApplicationChannel,
        *,
        credential: str,
    ) -> ApplicationChannel:
        run = self._require_credential(credential)
        if run.state not in {ApplicationState.STARTING, ApplicationState.RUNNING}:
            raise SessionOccupiedError(
                f"Application session is not connectable: {run.state.value}"
            )
        if channel in run.connected_channels:
            raise SessionOccupiedError(
                f"Application channel is already connected: {channel.value}"
            )

        run.connected_channels.add(channel)
        if run.connected_channels == set(ApplicationChannel):
            run.state = ApplicationState.RUNNING
        return channel

    def detach_channel(
        self,
        channel: ApplicationChannel,
        *,
        abnormal: bool = True,
    ) -> None:
        run = self._active_run
        if run is None or channel not in run.connected_channels:
            return
        run.connected_channels.remove(channel)
        if abnormal and run.state in {
            ApplicationState.STARTING,
            ApplicationState.RUNNING,
        }:
            run.state = ApplicationState.ERROR

    def end_run(self, final_state: ApplicationState) -> ApplicationRun:
        if final_state not in {ApplicationState.ENDED, ApplicationState.ERROR}:
            raise ValueError("final_state must be ended or error")
        run = self._active_run
        if run is None:
            raise ApplicationSessionError("no Application session exists")
        run.state = final_state
        run.connected_channels.clear()
        self._active_run = None
        return run

    def _require_credential(self, credential: str) -> ApplicationRun:
        run = self._active_run
        if run is None or not secrets.compare_digest(
            run.credential,
            str(credential or ""),
        ):
            raise InvalidRunCredentialError(
                "credential does not belong to the current Application run"
            )
        return run
