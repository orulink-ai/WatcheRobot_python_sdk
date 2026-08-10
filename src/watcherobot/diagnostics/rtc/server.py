"""Loopback-only HTTP host for the RTC diagnostics page."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from watcherobot import __version__

from .protocol import RTC_PROTOCOL


class RtcDiagnosticsServer:
    """Serve immutable SDK assets without exposing a LAN listener."""

    def __init__(
        self,
        *,
        control_url: str,
        external_url: str,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("RTC diagnostics must bind to 127.0.0.1")
        if not 0 <= port <= 65535:
            raise ValueError("port must be within 0..65535")
        self._host = host
        self._port = port
        self._config = {
            "controlUrl": _loopback_url(control_url, scheme="http"),
            "externalUrl": _loopback_url(external_url, scheme="ws"),
            "protocol": RTC_PROTOCOL,
            "pythonSdkVersion": __version__,
            "pythonSdkCommit": os.environ.get("WATCHEROBOT_SDK_COMMIT", ""),
        }
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        config = dict(self._config)

        class Handler(_RtcRequestHandler):
            runtime_config = config

        httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="watcher-rtc-diagnostics",
            daemon=True,
        )
        self._httpd = httpd
        self._thread = thread
        thread.start()
        return self.url

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("RTC diagnostics server is not running")
        port = int(self._httpd.server_address[1])
        return f"http://{self._host}:{port}"

    def wait(self) -> None:
        thread = self._thread
        if thread is None:
            raise RuntimeError("RTC diagnostics server is not running")
        while thread.is_alive():
            thread.join(timeout=0.5)

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


def run_rtc_diagnostics(
    *,
    control_url: str,
    external_url: str,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    server = RtcDiagnosticsServer(
        control_url=control_url,
        external_url=external_url,
        port=port,
    )
    url = server.start()
    print(f"RTC diagnostics: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url, new=2)
    try:
        server.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        server.stop()
    return 0


def _loopback_url(value: str, *, scheme: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {scheme, f"{scheme}s"} or parsed.port is None:
        raise ValueError(f"invalid {scheme} Runtime URL")
    netloc = f"127.0.0.1:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


class _RtcRequestHandler(BaseHTTPRequestHandler):
    runtime_config: dict[str, str] = {}
    _ASSETS = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
    }

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        path = self.path.split("?", 1)[0]
        if path == "/config.json":
            self._send(
                json.dumps(self.runtime_config, separators=(",", ":")).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/healthz":
            self._send(b'{"ok":true}', "application/json; charset=utf-8")
            return
        asset = self._ASSETS.get(path)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        name, content_type = asset
        static_root = resources.files("watcherobot.diagnostics.rtc.static")
        self._send(static_root.joinpath(name).read_bytes(), content_type)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self' http://127.0.0.1:* "
            "ws://127.0.0.1:*; img-src 'self' blob: data:; media-src blob:; "
            "style-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: Any) -> None:
        del args
