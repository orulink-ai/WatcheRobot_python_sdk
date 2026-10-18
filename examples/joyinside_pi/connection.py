"""Pairing adapter for the Daemon-injected local management endpoint."""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ConnectionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_pairing_code(code: Any) -> str:
    if not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None:
        raise ConnectionError("请输入机器人屏幕上的六位数字配对码", 400)
    return code


class DaemonConnection:
    """Manage pairing only; robot business calls remain on ApplicationContext."""

    def __init__(self, status_url: str, *, opener: Callable[..., Any] = urlopen) -> None:
        parsed = urlsplit(status_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.path != "/daemon/devices" or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("Daemon must inject a loopback device management URL")
        self.status_url = status_url
        self._opener = opener

    def status(self) -> dict[str, Any]:
        return self._request()

    def pair(self, code: str) -> dict[str, Any]:
        return self._request("/pair", {
            "pairing_code": validate_pairing_code(code), "target_mode": "python_sdk",
        })

    def _request(self, action: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(self.status_url + action,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with self._opener(request, timeout=3) as response:
                result = json.load(response)
            device = result.get("device") if isinstance(result, dict) else None
            if not isinstance(device, dict):
                raise ValueError("invalid device response")
            # Management responses can contain transport URLs and credentials.
            state = device.get("state")
            error = device.get("last_error")
            safe_errors = {"pairing_not_found", "device_connect_timeout",
                           "pairing_timeout", "discovery_timeout", "connect_timeout", "reconnect_timeout",
                           "device_not_found", "device_busy", "pairing_cancelled", "reunite_unavailable"}
            return {
                "state": state if state in {"idle", "discovering", "connecting", "connected", "reconnecting"} else "unavailable",
                "online": device.get("online") is True,
                "device_id": device.get("device_id") if isinstance(device.get("device_id"), str) else "",
                "last_error": error if error in safe_errors else ("connection_failed" if error else None),
            }
        except HTTPError as error:
            if error.code == 409:
                raise ConnectionError("机器人已连接或正在连接，请等待当前连接完成", 409) from None
            if 400 <= error.code < 500:
                raise ConnectionError("配对请求未被接受，请检查机器人配对码后重试", 400) from None
            raise ConnectionError("本机连接服务暂时不可用，请稍后重试") from None
        except (OSError, ValueError, TypeError):
            raise ConnectionError("无法读取本机连接服务，请确认 Daemon 正在运行") from None
