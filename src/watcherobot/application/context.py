"""Public context for a Daemon-managed WatcheRobot Application."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from types import TracebackType

from watcherobot.application.desktop import ApplicationDesktop
from watcherobot.application.rtc import ApplicationRtc
from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.robot import WatcheRobot


class ApplicationEnvironmentError(RuntimeError):
    """Raised when code is not running inside an authorized Application."""


class ApplicationContext:
    """Own the SDK objects injected into one managed Application process."""

    def __init__(
        self,
        *,
        app_id: str,
        transport: DaemonApplicationTransport,
    ) -> None:
        self.app_id = app_id
        self._transport = transport
        self.robot = WatcheRobot._from_transport(transport)
        self.desktop = ApplicationDesktop(transport)
        self.rtc = ApplicationRtc(transport)
        self.logger = _build_application_logger(app_id)
        self._entered = False
        shutdown_signal = os.environ.get("WATCHER_APP_SHUTDOWN_SIGNAL", "").strip()
        self._shutdown_signal = Path(shutdown_signal) if shutdown_signal else None

    @property
    def shutdown_requested(self) -> bool:
        """Return whether the Daemon requested this run to stop gracefully."""

        return self._shutdown_signal is not None and self._shutdown_signal.exists()

    @classmethod
    def from_environment(cls) -> "ApplicationContext":
        required = {
            "WATCHER_APP_ID": os.environ.get("WATCHER_APP_ID", "").strip(),
            "WATCHER_APP_RUN_CREDENTIAL": os.environ.get(
                "WATCHER_APP_RUN_CREDENTIAL",
                "",
            ).strip(),
            "WATCHER_APP_DESKTOP_WS_URL": os.environ.get(
                "WATCHER_APP_DESKTOP_WS_URL",
                "",
            ).strip(),
            "WATCHER_APP_DEVICE_WS_URL": os.environ.get(
                "WATCHER_APP_DEVICE_WS_URL",
                "",
            ).strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ApplicationEnvironmentError(
                "Application environment is incomplete: "
                + ", ".join(sorted(missing))
            )
        return cls(
            app_id=required["WATCHER_APP_ID"],
            transport=DaemonApplicationTransport(),
        )

    async def __aenter__(self) -> "ApplicationContext":
        if self._entered:
            raise RuntimeError("ApplicationContext is already entered")
        await asyncio.to_thread(self._transport.start)
        self._entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await asyncio.to_thread(self.close)

    def close(self) -> None:
        if not self._entered:
            return
        self.rtc.close()
        self.robot.close()
        self._entered = False


def _build_application_logger(app_id: str) -> logging.Logger:
    logger = logging.getLogger(f"watcherobot.application.{app_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    return logger
