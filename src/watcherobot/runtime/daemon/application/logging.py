"""Application process log persistence and one-way desktop forwarding."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path


DesktopLogForwarder = Callable[[str], Awaitable[object] | None]


class ApplicationLogService:
    """Persist every process log before attempting best-effort forwarding."""

    def __init__(
        self,
        *,
        log_dir: Path,
        desktop_forwarder: DesktopLogForwarder,
    ) -> None:
        self._log_dir = Path(log_dir)
        self._desktop_forwarder = desktop_forwarder
        self._write_lock = asyncio.Lock()
        self.last_write_error: str | None = None
        self.last_forward_error: str | None = None

    def log_path(self, app_id: str) -> Path:
        safe_app_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", app_id).strip("._")
        return self._log_dir / f"{safe_app_id or 'application'}.jsonl"

    def read_recent(self, app_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        """Read a bounded tail of persisted stdout/stderr records."""

        bounded_limit = max(1, min(int(limit), 500))
        path = self.log_path(app_id)
        if not path.is_file():
            return []
        records: deque[dict[str, object]] = deque(maxlen=bounded_limit)
        try:
            with path.open("r", encoding="utf-8") as log_file:
                for line in log_file:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        records.append(record)
        except OSError as exc:
            self.last_write_error = str(exc)
            return []
        return list(records)

    async def record(
        self,
        *,
        app_id: str,
        stream: str,
        message: str,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app_id": app_id,
            "stream": stream,
            "message": message,
        }
        encoded_record = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            async with self._write_lock:
                await asyncio.to_thread(
                    self._append_record,
                    self.log_path(app_id),
                    encoded_record,
                )
        except Exception as exc:
            self.last_write_error = str(exc)

        event = json.dumps(
            {
                "type": "daemon.application.log",
                "code": 0,
                "data": record,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            result = self._desktop_forwarder(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self.last_forward_error = str(exc)

    @staticmethod
    def _append_record(path: Path, record: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(record)
            log_file.write("\n")
