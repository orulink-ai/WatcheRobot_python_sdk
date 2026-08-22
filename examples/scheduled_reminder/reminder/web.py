"""闹钟设置的本地网页服务：纯标准库 HTTP 服务器 + 简单 JSON REST API。

路由：
- ``GET  /``                  设置页面（web/index.html）
- ``GET  /api/alarms``        闹钟列表
- ``POST /api/alarms``        新增（body: time/text/days/enabled）
- ``PATCH /api/alarms/<id>``  更新（body 里带哪个字段就更新哪个）
- ``DELETE /api/alarms/<id>`` 删除
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from reminder.alarms import (
    AlarmStore,
    AlarmValidationError,
    to_dict,
)

INDEX_HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"

_UNSET = object()


class AlarmHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], store: AlarmStore) -> None:
        super().__init__(server_address, AlarmRequestHandler)
        self.store = store


class AlarmRequestHandler(BaseHTTPRequestHandler):
    server_version = "RobotAlarm/1.0"

    # -- 工具 --
    def _store(self) -> AlarmStore:
        return self.server.store  # type: ignore[attr-defined]

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AlarmValidationError("请求体必须是合法 JSON") from None
        return payload if isinstance(payload, dict) else {}

    def _send(self, status: int, body: Any = None) -> None:
        if status == 204:
            self.send_response(status)
            self.end_headers()
            return
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_page(self) -> None:
        try:
            content = INDEX_HTML.read_bytes()
        except OSError:
            content = b"<h1>index.html missing</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    # -- 路由 --
    def do_GET(self) -> None:  # noqa: N802 - http.server 命名约定
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._serve_page()
            return
        if path == "/api/alarms":
            self._send(200, {"alarms": [to_dict(alarm) for alarm in self._store().all()]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/alarms":
            self._send(404, {"error": "not found"})
            return
        try:
            body = self._read_json()
            time_value = body.get("time", _UNSET)
            text_value = body.get("text", _UNSET)
            if time_value is _UNSET or text_value is _UNSET:
                self._send(400, {"error": "缺少字段 time 或 text"})
                return
            alarm = self._store().add(
                time=time_value,
                text=text_value,
                days=body.get("days", ()),
                enabled=body.get("enabled", True),
            )
        except AlarmValidationError as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(201, {"alarm": to_dict(alarm)})

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        alarm_id = _alarm_id_from(path)
        if alarm_id is None:
            self._send(404, {"error": "not found"})
            return
        try:
            body = self._read_json()
            fields: dict[str, Any] = {}
            if "enabled" in body:
                fields["enabled"] = bool(body["enabled"])
            if "time" in body:
                fields["time"] = body["time"]
            if "text" in body:
                fields["text"] = body["text"]
            if "days" in body:
                fields["days"] = body["days"]
            updated = self._store().update(alarm_id, **fields)
        except AlarmValidationError as exc:
            self._send(400, {"error": str(exc)})
            return
        if updated is None:
            self._send(404, {"error": "alarm not found"})
            return
        self._send(200, {"alarm": to_dict(updated)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        alarm_id = _alarm_id_from(path)
        if alarm_id is None:
            self._send(404, {"error": "not found"})
            return
        if self._store().remove(alarm_id):
            self._send(204)
            return
        self._send(404, {"error": "alarm not found"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 保持 http.server 签名
        # 应用日志走 app.logger，这里静默，避免刷屏。
        return


def _alarm_id_from(path: str) -> str | None:
    prefix = "/api/alarms/"
    if not path.startswith(prefix):
        return None
    alarm_id = unquote(path[len(prefix):])
    return alarm_id or None


class AlarmWebServer:
    """在后台线程跑一个本地网页服务，提供闹钟设置页面与 REST API。"""

    def __init__(self, store: AlarmStore, host: str = "127.0.0.1", port: int = 8766) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._httpd: AlarmHttpServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        """绑定并启动服务线程；端口为 0 时使用系统分配的临时端口。"""
        httpd = AlarmHttpServer((self.host, self.port), self.store)
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="alarm-web-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None


__all__ = ["AlarmWebServer", "INDEX_HTML"]