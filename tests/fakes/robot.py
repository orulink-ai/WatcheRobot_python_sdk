from __future__ import annotations

import inspect
import json
from typing import Any

import websockets

from watcherobot.protocol import PROTOCOL_VERSION


def _loopback_connect(uri: str) -> Any:
    """Connect directly to a local gateway even when the host configures a proxy."""
    options: dict[str, Any] = {}
    if "proxy" in inspect.signature(websockets.connect).parameters:
        options["proxy"] = None
    return websockets.connect(uri, **options)


class FakeRobot:
    """Small robot-side protocol peer used by gateway integration tests."""

    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket

    @classmethod
    async def connect(cls, port: int) -> FakeRobot:
        websocket = await _loopback_connect(f"ws://127.0.0.1:{port}")
        return cls(websocket)

    async def close(self) -> None:
        await self.websocket.close()

    async def __aenter__(self) -> FakeRobot:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def begin_pairing(
        self,
        *,
        device_id: str = "watcher-test",
        firmware_version: str = "V1.0",
        mac: str = "00:11:22:33:44:55",
    ) -> dict[str, Any]:
        await self.send(
            "sys.client.hello",
            {
                "device_id": device_id,
                "fw_version": firmware_version,
                "mac": mac,
            },
        )
        hello_ack = await self.receive()
        assert hello_ack["type"] == "sys.ack"
        assert hello_ack["data"]["type"] == "sys.client.hello"
        authenticate = await self.receive()
        assert authenticate["type"] == "sys.sdk.authenticate"
        return authenticate

    async def accept_pairing(
        self,
        authenticate: dict[str, Any],
        *,
        expected_pairing_code: str = "123456",
        capabilities: tuple[str, ...] = ("behavior", "motion"),
    ) -> None:
        assert authenticate["data"]["pairing_code"] == expected_pairing_code
        await self.ack(authenticate)
        await self.send(
            "evt.sdk.ready",
            {
                "protocol_version": PROTOCOL_VERSION,
                "device_id": "watcher-test",
                "firmware_version": "V1.0",
                "capabilities": list(capabilities),
            },
        )

    async def receive(self) -> dict[str, Any]:
        raw = await self.websocket.recv()
        if not isinstance(raw, str):
            raise AssertionError("expected a JSON text command from the SDK")
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise AssertionError("expected a JSON object from the SDK")
        return message

    async def send(self, message_type: str, data: dict[str, Any], *, code: int = 0) -> None:
        await self.websocket.send(
            json.dumps({"type": message_type, "code": code, "data": data}, separators=(",", ":"))
        )

    async def ack(self, command: dict[str, Any], *, operation_id: int | None = None) -> None:
        data: dict[str, Any] = {
            "type": command["type"],
            "command_id": command["data"]["command_id"],
        }
        if operation_id is not None:
            data["operation_id"] = operation_id
        await self.send("sys.ack", data)

    async def nack(self, command: dict[str, Any], *, reason: str) -> None:
        await self.send(
            "sys.nack",
            {
                "type": command["type"],
                "command_id": command["data"]["command_id"],
                "reason": reason,
            },
            code=1,
        )

    async def operation(
        self,
        operation_id: int,
        state: str,
        *,
        domain: str = "behavior",
        error_code: int | None = None,
        reason: str | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "operation_id": operation_id,
            "domain": domain,
            "state": state,
        }
        if error_code is not None:
            data["error_code"] = error_code
        if reason is not None:
            data["reason"] = reason
        await self.send("evt.sdk.operation", data)
