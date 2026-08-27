"""Strict watcher-lan-pairing wire contract.

This module intentionally has no socket or lifecycle dependencies.  UDP and
WebSocket owners validate untrusted payloads here before changing runtime
state.

Version matrix (see workspace docs/lan-fast-reconnect-protocol-design.md):

- The four legacy ``pair.*`` datagrams are frozen at ``version="1.0"``.
- ``link.reunite.request``/``link.reunite.accept`` (fast reconnect) always use
  ``version="1.1"``.
- Hardware hello accepts ``pairing_version`` of either generation because the
  reported value mirrors whichever exchange established the current session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


LAN_PAIRING_PROTOCOL = "watcher-lan-pairing"
LAN_PAIRING_VERSION = "1.0"
LAN_PAIRING_REUNITE_VERSION = "1.1"
LAN_PAIRING_HELLO_VERSIONS = frozenset(
    {LAN_PAIRING_VERSION, LAN_PAIRING_REUNITE_VERSION}
)
LAN_PAIRING_TARGET_MODE_DESKTOP_LINK = "desktop_link"
LAN_PAIRING_TARGET_MODE_PYTHON_SDK = "python_sdk"
LAN_PAIRING_TARGET_MODES = frozenset(
    {
        LAN_PAIRING_TARGET_MODE_DESKTOP_LINK,
        LAN_PAIRING_TARGET_MODE_PYTHON_SDK,
    }
)
MAX_UDP_PAYLOAD_BYTES = 2048
MAX_HELLO_PAYLOAD_BYTES = 4096
_BINDING_SECRET_KDF_INFO = b"watcher-link-binding-v1"
_REUNITE_MAC_PREFIX = "watcher-link-reunite-v1"

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
_LEGACY_UDP_TYPES = ("pair.request", "pair.accept", "pair.busy", "pair.cancel")
_UDP_FIELDS = {
    "pair.request": _COMMON_UDP_FIELDS
    | {"pairing_code", "target_mode", "websocket_port"},
    "pair.accept": _COMMON_UDP_FIELDS | {"target_mode", "session_token"},
    "pair.busy": _COMMON_UDP_FIELDS | {"reason"},
    "pair.cancel": _COMMON_UDP_FIELDS | {"session_token"},
    "link.reunite.request": _COMMON_UDP_FIELDS
    | {"nonce", "target_mode", "websocket_port"},
    "link.reunite.accept": _COMMON_UDP_FIELDS
    | {"nonce", "target_mode", "response_mac", "session_token"},
}
# Legacy datagrams stay frozen on 1.0; reunite datagrams always speak 1.1.
_UDP_TYPE_VERSIONS: dict[str, str] = {
    message_type: LAN_PAIRING_VERSION for message_type in _LEGACY_UDP_TYPES
}
_UDP_TYPE_VERSIONS.update(
    {
        "link.reunite.request": LAN_PAIRING_REUNITE_VERSION,
        "link.reunite.accept": LAN_PAIRING_REUNITE_VERSION,
    }
)
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
class LinkReuniteRequest:
    """Daemon broadcast asking a remembered device to reconnect without a code."""

    request_id: str
    daemon_instance_id: str
    nonce: str
    target_mode: str
    websocket_port: int


@dataclass(frozen=True)
class LinkReuniteAccept:
    """Device reply proving knowledge of the long-term binding secret."""

    request_id: str
    daemon_instance_id: str
    nonce: str
    target_mode: str
    response_mac: str
    session_token: str


@dataclass(frozen=True)
class HardwareHello:
    pair_request_id: str
    daemon_instance_id: str
    session_token: str
    mode: str


@dataclass(frozen=True)
class DeviceSessionEnd:
    pair_request_id: str
    reason: str


UdpMessage = (
    PairRequest
    | PairAccept
    | PairBusy
    | PairCancel
    | LinkReuniteRequest
    | LinkReuniteAccept
)
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


def _require_target_mode(payload: Mapping[str, object], field: str) -> str:
    value = _require_string(payload, field)
    if value not in LAN_PAIRING_TARGET_MODES:
        raise PairingProtocolError(f"{field} is unsupported: {value}")
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


def _validate_udp_common(
    payload: Mapping[str, object],
    *,
    version: str,
) -> tuple[str, str]:
    _require_literal(payload, "protocol", LAN_PAIRING_PROTOCOL)
    _require_literal(payload, "version", version)
    request_id = _require_lower_hex(payload, "request_id", _LOWER_HEX_32)
    daemon_instance_id = _require_lower_hex(
        payload,
        "daemon_instance_id",
        _LOWER_HEX_32,
    )
    return request_id, daemon_instance_id


def _require_websocket_port(payload: Mapping[str, object]) -> int:
    websocket_port = payload.get("websocket_port")
    if type(websocket_port) is not int or not 1 <= websocket_port <= 65535:
        raise PairingProtocolError("websocket_port must be within 1..65535")
    return websocket_port


def parse_udp_message(payload: RawPayload) -> UdpMessage:
    """Parse one strict UDP pairing message without changing runtime state."""

    message = _decode_object(payload, max_bytes=MAX_UDP_PAYLOAD_BYTES)
    message_type = _require_string(message, "type")
    expected_fields = _UDP_FIELDS.get(message_type)
    if expected_fields is None:
        raise PairingProtocolError(f"type is unsupported: {message_type}")
    _require_exact_fields(message, expected_fields)
    request_id, daemon_instance_id = _validate_udp_common(
        message,
        version=_UDP_TYPE_VERSIONS[message_type],
    )

    if message_type == "pair.request":
        pairing_code = _require_string(message, "pairing_code")
        if _PAIRING_CODE.fullmatch(pairing_code) is None:
            raise PairingProtocolError("pairing_code must contain six digits")
        target_mode = _require_target_mode(message, "target_mode")
        return PairRequest(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            pairing_code=pairing_code,
            target_mode=target_mode,
            websocket_port=_require_websocket_port(message),
        )

    if message_type == "pair.accept":
        return PairAccept(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            target_mode=_require_target_mode(message, "target_mode"),
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

    if message_type == "pair.cancel":
        return PairCancel(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            session_token=_require_lower_hex(
                message,
                "session_token",
                _LOWER_HEX_64,
            ),
        )

    if message_type == "link.reunite.request":
        return LinkReuniteRequest(
            request_id=request_id,
            daemon_instance_id=daemon_instance_id,
            nonce=_require_lower_hex(message, "nonce", _LOWER_HEX_32),
            target_mode=_require_target_mode(message, "target_mode"),
            websocket_port=_require_websocket_port(message),
        )

    return LinkReuniteAccept(
        request_id=request_id,
        daemon_instance_id=daemon_instance_id,
        nonce=_require_lower_hex(message, "nonce", _LOWER_HEX_32),
        target_mode=_require_target_mode(message, "target_mode"),
        response_mac=_require_lower_hex(message, "response_mac", _LOWER_HEX_64),
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
    pairing_version = _require_string(data, "pairing_version")
    if pairing_version not in LAN_PAIRING_HELLO_VERSIONS:
        raise PairingProtocolError(
            f"pairing_version is unsupported: {pairing_version}"
        )

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
        mode=_require_target_mode(data, "mode"),
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
    elif isinstance(message, LinkReuniteRequest):
        payload = {
            "type": "link.reunite.request",
            **common,
            "version": LAN_PAIRING_REUNITE_VERSION,
            "nonce": message.nonce,
            "target_mode": message.target_mode,
            "websocket_port": message.websocket_port,
        }
    elif isinstance(message, LinkReuniteAccept):
        payload = {
            "type": "link.reunite.accept",
            **common,
            "version": LAN_PAIRING_REUNITE_VERSION,
            "nonce": message.nonce,
            "target_mode": message.target_mode,
            "response_mac": message.response_mac,
            "session_token": message.session_token,
        }
    else:
        raise TypeError(f"unsupported UDP pairing message: {type(message)!r}")
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def derive_binding_secret(session_token: str) -> str:
    """Derive the long-term reconnect secret from a manual pairing token.

    Mirrors ``binding_secret`` in the watcher-lan-pairing/1.1 fast reconnect
    design; both ends must compute the identical value from the session token
    that was established by manual pairing (never by a reunite exchange).
    """

    return hmac.new(
        session_token.encode("ascii"),
        _BINDING_SECRET_KDF_INFO,
        hashlib.sha256,
    ).hexdigest()


def reunite_response_mac(
    binding_secret: str,
    *,
    request_id: str,
    nonce: str,
    daemon_instance_id: str,
    target_mode: str,
) -> str:
    """Compute the constant/link-agnostic MAC answering one reunite challenge."""

    message = "|".join(
        (
            _REUNITE_MAC_PREFIX,
            request_id,
            nonce,
            daemon_instance_id,
            target_mode,
        )
    )
    return hmac.new(
        binding_secret.encode("ascii"),
        message.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


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
                }
            },
        },
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
