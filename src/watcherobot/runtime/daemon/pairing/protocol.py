"""Strict watcher-lan-pairing/1.0 wire contract.

This module intentionally has no socket or lifecycle dependencies.  UDP and
WebSocket owners validate untrusted payloads here before changing runtime
state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


LAN_PAIRING_PROTOCOL = "watcher-lan-pairing"
LAN_PAIRING_VERSION = "1.0"
LAN_PAIRING_TARGET_MODE = "desktop_link"
MAX_UDP_PAYLOAD_BYTES = 2048
MAX_HELLO_PAYLOAD_BYTES = 4096

_LOWER_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_PAIRING_CODE = re.compile(r"^[0-9]{6}$")
_COMMON_UDP_FIELDS = frozenset(
    {
        "type",
        "protocol",
        "version",
        "request_id",
        "daemon_instance_id",
    }
)
_UDP_FIELDS = {
    "pair.request": _COMMON_UDP_FIELDS
    | {"pairing_code", "target_mode", "websocket_port"},
    "pair.accept": _COMMON_UDP_FIELDS | {"target_mode", "session_token"},
    "pair.busy": _COMMON_UDP_FIELDS | {"reason"},
    "pair.cancel": _COMMON_UDP_FIELDS | {"session_token"},
}
_HELLO_FIELDS = frozenset({"type", "code", "data"})
_HELLO_DATA_FIELDS = frozenset(
    {
        "role",
        "pairing_protocol",
        "pairing_version",
        "pair_request_id",
        "daemon_instance_id",
        "session_token",
        "mode",
    }
)
_MEDIA_HELLO_DATA_FIELDS = frozenset(
    {
        "pairing_protocol",
        "pairing_version",
        "pair_request_id",
        "daemon_instance_id",
        "session_token",
        "mode",
        "channel",
        "version",
    }
)
_SESSION_END_FIELDS = frozenset({"type", "code", "data"})
_SESSION_END_DATA_FIELDS = frozenset({"pair_request_id", "reason"})
_SENSITIVE_FIELDS = frozenset({"pairing_code", "session_token"})


class PairingProtocolError(ValueError):
    """Raised when an untrusted pairing payload violates the fixed contract."""


@dataclass(frozen=True)
class PairRequest:
    request_id: str
    daemon_instance_id: str
    pairing_code: str
    target_mode: str
    websocket_port: int


@dataclass(frozen=True)
class PairAccept:
    request_id: str
    daemon_instance_id: str
    target_mode: str
    session_token: str


@dataclass(frozen=True)
class PairBusy:
    request_id: str
    daemon_instance_id: str
    reason: str


@dataclass(frozen=True)
class PairCancel:
    request_id: str
    daemon_instance_id: str
    session_token: str


@dataclass(frozen=True)
class HardwareHello:
    pair_request_id: str
    daemon_instance_id: str
    session_token: str
    mode: str


@dataclass(frozen=True)
class MediaHello:
    pair_request_id: str
    daemon_instance_id: str
    session_token: str
    mode: str
    channel: str
    version: int


@dataclass(frozen=True)
class DeviceSessionEnd:
    pair_request_id: str
    reason: str


UdpMessage = PairRequest | PairAccept | PairBusy | PairCancel
RawPayload = Mapping[str, object] | str | bytes


def _decode_object(payload: RawPayload, *, max_bytes: int) -> dict[str, object]:
    if isinstance(payload, bytes):
        if len(payload) > max_bytes:
            raise PairingProtocolError("payload exceeds maximum length")
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PairingProtocolError("payload must be UTF-8") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > max_bytes:
            raise PairingProtocolError("payload exceeds maximum length")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PairingProtocolError("payload must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise PairingProtocolError("payload must be a JSON object")
    return dict(payload)


def _require_exact_fields(
    payload: Mapping[str, object],
    expected: frozenset[str] | set[str],
) -> None:
    actual = set(payload)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise PairingProtocolError(f"unknown fields: {', '.join(unknown)}")
    if missing:
        raise PairingProtocolError(f"missing fields: {', '.join(missing)}")


def _require_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise PairingProtocolError(f"{field} must be a string")
    return value


def _require_literal(
    payload: Mapping[str, object],
    field: str,
    expected: str,
) -> str:
    value = _require_string(payload, field)
    if value != expected:
        raise PairingProtocolError(f"{field} must be {expected}")
    return value


def _require_lower_hex(
    payload: Mapping[str, object],
    field: str,
    pattern: re.Pattern[str],
) -> str:
    value = _require_string(payload, field)
    if pattern.fullmatch(value) is None:
        raise PairingProtocolError(f"{field} has invalid format")
    return value


def _validate_udp_common(payload: Mapping[str, object]) -> tuple[str, str]:
    _require_literal(payload, "protocol", LAN_PAIRING_PROTOCOL)
    _require_literal(payload, "version", LAN_PAIRING_VERSION)
    request_id = _require_lower_hex(payload, "request_id", _LOWER_HEX_32)
    daemon_instance_id = _require_lower_hex(
        payload,
        "daemon_instance_id",
        _LOWER_HEX_32,
    )
    return request_id, daemon_instance_id


def parse_udp_message(payload: RawPayload) -> UdpMessage:
    """Parse one strict UDP pairing message without changing runtime state."""

    message = _decode_object(payload, max_bytes=MAX_UDP_PAYLOAD_BYTES)
    message_type = _require_string(message, "type")
    expected_fields = _UDP_FIELDS.get(message_type)
    if expected_fields is None:
        raise PairingProtocolError(f"type is unsupported: {message_type}")
    _require_exact_fields(message, expected_fields)
    request_id, daemon_instance_id = _validate_udp_common(message)

    if message_type == "pair.request":
        pairing_code = _require_string(message, "pairing_code")
        if _PAIRING_CODE.fullmatch(pairing_code) is None:
            raise PairingProtocolError("pairing_code must contain six digits")
        target_mode = _require_literal(
            message,
            "target_mode",
            LAN_PAIRING_TARGET_MODE,
        )
        websocket_port = message.get("websocket_port")
        if type(websocket_port) is not int or not 1 <= websocket_port <= 65535:
            raise PairingProtocolError("websocket_port must be within 1..65535")
        return PairRequest(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            pairing_code=pairing_code,
            target_mode=target_mode,
            websocket_port=websocket_port,
        )

    if message_type == "pair.accept":
        return PairAccept(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            target_mode=_require_literal(
                message,
                "target_mode",
                LAN_PAIRING_TARGET_MODE,
            ),
            session_token=_require_lower_hex(
                message,
                "session_token",
                _LOWER_HEX_64,
            ),
        )

    if message_type == "pair.busy":
        return PairBusy(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            reason=_require_literal(
                message,
                "reason",
                "device_session_active",
            ),
        )

    return PairCancel(
        request_id=request_id,
        daemon_instance_id=daemon_instance_id,
        session_token=_require_lower_hex(
            message,
            "session_token",
            _LOWER_HEX_64,
        ),
    )


def parse_hardware_hello(payload: RawPayload) -> HardwareHello:
    """Parse a hardware hello carrying only the in-memory pairing session."""

    message = _decode_object(payload, max_bytes=MAX_HELLO_PAYLOAD_BYTES)
    _require_exact_fields(message, _HELLO_FIELDS)
    _require_literal(message, "type", "sys.client.hello")
    if type(message.get("code")) is not int or message["code"] != 0:
        raise PairingProtocolError("code must be integer zero")

    data = message.get("data")
    if not isinstance(data, Mapping):
        raise PairingProtocolError("data must be a JSON object")
    data = dict(data)
    _require_exact_fields(data, _HELLO_DATA_FIELDS)
    _require_literal(data, "role", "hardware")
    _require_literal(data, "pairing_protocol", LAN_PAIRING_PROTOCOL)
    _require_literal(data, "pairing_version", LAN_PAIRING_VERSION)

    return HardwareHello(
        pair_request_id=_require_lower_hex(
            data,
            "pair_request_id",
            _LOWER_HEX_32,
        ),
        daemon_instance_id=_require_lower_hex(
            data,
            "daemon_instance_id",
            _LOWER_HEX_32,
        ),
        session_token=_require_lower_hex(
            data,
            "session_token",
            _LOWER_HEX_64,
        ),
        mode=_require_literal(data, "mode", LAN_PAIRING_TARGET_MODE),
    )


def parse_media_hello(payload: RawPayload) -> MediaHello:
    """Parse a video sidecar hello bound to an online control session."""

    message = _decode_object(payload, max_bytes=MAX_HELLO_PAYLOAD_BYTES)
    _require_exact_fields(message, _HELLO_FIELDS)
    _require_literal(message, "type", "sys.media.hello")
    if type(message.get("code")) is not int or message["code"] != 0:
        raise PairingProtocolError("code must be integer zero")
    data = message.get("data")
    if not isinstance(data, Mapping):
        raise PairingProtocolError("data must be a JSON object")
    data = dict(data)
    _require_exact_fields(data, _MEDIA_HELLO_DATA_FIELDS)
    _require_literal(data, "pairing_protocol", LAN_PAIRING_PROTOCOL)
    _require_literal(data, "pairing_version", LAN_PAIRING_VERSION)
    channel = _require_literal(data, "channel", "video")
    version = data.get("version")
    if type(version) is not int or version != 1:
        raise PairingProtocolError("version must be integer one")
    return MediaHello(
        pair_request_id=_require_lower_hex(data, "pair_request_id", _LOWER_HEX_32),
        daemon_instance_id=_require_lower_hex(data, "daemon_instance_id", _LOWER_HEX_32),
        session_token=_require_lower_hex(data, "session_token", _LOWER_HEX_64),
        mode=_require_literal(data, "mode", LAN_PAIRING_TARGET_MODE),
        channel=channel,
        version=version,
    )


def parse_device_session_end(payload: RawPayload) -> DeviceSessionEnd:
    """Parse the hardware's explicit normal session termination."""

    message = _decode_object(payload, max_bytes=MAX_HELLO_PAYLOAD_BYTES)
    _require_exact_fields(message, _SESSION_END_FIELDS)
    _require_literal(message, "type", "sys.device.session.end")
    if type(message.get("code")) is not int or message["code"] != 0:
        raise PairingProtocolError("code must be integer zero")

    data = message.get("data")
    if not isinstance(data, Mapping):
        raise PairingProtocolError("data must be a JSON object")
    data = dict(data)
    _require_exact_fields(data, _SESSION_END_DATA_FIELDS)
    return DeviceSessionEnd(
        pair_request_id=_require_lower_hex(
            data,
            "pair_request_id",
            _LOWER_HEX_32,
        ),
        reason=_require_literal(data, "reason", "mode_exit"),
    )


def redact_sensitive_fields(value: Any) -> Any:
    """Return a detached copy safe for diagnostics and structured logs."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for field, child in value.items():
            if field == "pairing_code":
                redacted[field] = "[redacted]"
            elif field == "session_token" and isinstance(child, str):
                redacted[field] = f"[redacted:{child[-4:]}]"
            else:
                redacted[field] = redact_sensitive_fields(child)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_fields(item) for item in value)
    return value


def encode_udp_message(message: UdpMessage) -> bytes:
    """Serialize an already validated UDP message into one compact datagram."""

    common = {
        "protocol": LAN_PAIRING_PROTOCOL,
        "version": LAN_PAIRING_VERSION,
        "request_id": message.request_id,
        "daemon_instance_id": message.daemon_instance_id,
    }
    if isinstance(message, PairRequest):
        payload = {
            "type": "pair.request",
            **common,
            "pairing_code": message.pairing_code,
            "target_mode": message.target_mode,
            "websocket_port": message.websocket_port,
        }
    elif isinstance(message, PairAccept):
        payload = {
            "type": "pair.accept",
            **common,
            "target_mode": message.target_mode,
            "session_token": message.session_token,
        }
    elif isinstance(message, PairBusy):
        payload = {
            "type": "pair.busy",
            **common,
            "reason": message.reason,
        }
    elif isinstance(message, PairCancel):
        payload = {
            "type": "pair.cancel",
            **common,
            "session_token": message.session_token,
        }
    else:
        raise TypeError(f"unsupported UDP pairing message: {type(message)!r}")
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def build_hardware_hello_ack() -> dict[str, object]:
    return {
        "type": "sys.ack",
        "code": 0,
        "data": {
            "type": "sys.client.hello",
            "role": "hardware",
            "session_state": "connected",
            "negotiated": {
                "audio_uplink": {
                    "codec": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration_ms": 60,
                    "packetization": "one_opus_packet_per_wspk",
                    "version": 1,
                },
                "video_uplink": {
                    "transport": "websocket_sidecar",
                    "hello_type": "sys.media.hello",
                    "version": 1,
                },
            },
        },
    }


def build_media_hello_ack() -> dict[str, object]:
    return {
        "type": "sys.ack",
        "code": 0,
        "data": {"type": "sys.media.hello", "channel": "video", "version": 1},
    }

def build_hardware_hello_nack(
    *,
    code: int,
    error: str,
) -> dict[str, object]:
    return {
        "type": "sys.nack",
        "code": code,
        "data": {
            "type": "sys.client.hello",
            "error": error,
        },
    }


def build_device_state_event(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    expected_fields = {
        "state",
        "online",
        "mode",
        "request_id",
        "last_error",
    }
    _require_exact_fields(snapshot, expected_fields)
    return {
        "type": "daemon.device.state",
        "code": 0,
        "data": dict(snapshot),
    }
