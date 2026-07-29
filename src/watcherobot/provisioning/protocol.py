"""Pure encoder and incremental decoder for the shipped BLE protocol."""

from __future__ import annotations

import codecs
import json
from json import JSONDecodeError
from typing import Any, cast

from .errors import PayloadTooLargeError, ProvisioningProtocolError
from .models import ProtocolMessage, WifiState

BLE_DEVICE_NAME = "ESP_ROBOT"
BLE_SERVICE_UUID = "000000ff-0000-1000-8000-00805f9b34fb"
BLE_CHARACTERISTIC_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
MAX_PROTOCOL_PAYLOAD_BYTES = 180
_MAX_RECEIVE_BUFFER_CHARS = 4096
_WIFI_STATES = {
    "connected",
    "connecting",
    "disconnected",
    "unconfigured",
}
_SAFE_NACK_REASONS = {
    "invalid_json",
    "invalid_payload",
    "invalid_wifi_payload",
    "unsupported_type",
    "wifi_clear_failed",
    "wifi_config_failed",
}


def build_request(message_type: str, data: dict[str, object]) -> bytes:
    """Encode one compact request without retaining its body."""

    if not message_type:
        raise ProvisioningProtocolError("Provisioning message type is empty")
    encoded = json.dumps(
        {"type": message_type, "data": data},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_PROTOCOL_PAYLOAD_BYTES:
        raise PayloadTooLargeError(
            len(encoded),
            MAX_PROTOCOL_PAYLOAD_BYTES,
        )
    return encoded


def parse_message(raw: str | bytes) -> ProtocolMessage:
    """Parse a message into a sanitized metadata-only representation."""

    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        envelope = json.loads(text)
    except (UnicodeDecodeError, JSONDecodeError) as exc:
        raise ProvisioningProtocolError(
            "Provisioning response is not valid UTF-8 JSON"
        ) from exc
    return _parse_envelope(envelope)


def _parse_envelope(envelope: Any) -> ProtocolMessage:
    if not isinstance(envelope, dict):
        raise ProvisioningProtocolError(
            "Provisioning response must be a JSON object"
        )
    message_type = envelope.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProvisioningProtocolError(
            "Provisioning response has no valid type"
        )
    data = envelope.get("data", {})
    if not isinstance(data, dict):
        raise ProvisioningProtocolError(
            "Provisioning response data must be an object"
        )

    code_value = envelope.get("code")
    code = code_value if isinstance(code_value, int) else None
    command_type = _optional_string(data.get("type"))
    command_id = _optional_string(data.get("command_id"))

    if message_type in {"sys.ack", "sys.nack"}:
        if code is None or command_type is None or command_id is None:
            raise ProvisioningProtocolError(
                "Provisioning acknowledgement is incomplete"
            )
        if message_type == "sys.ack" and code != 0:
            raise ProvisioningProtocolError(
                "Provisioning ACK contains a non-zero code"
            )
        if message_type == "sys.nack" and code == 0:
            raise ProvisioningProtocolError(
                "Provisioning NACK contains a zero code"
            )
        reason = _optional_string(data.get("reason"))
        if message_type == "sys.nack" and reason is None:
            reason = "rejected"
        if reason not in _SAFE_NACK_REASONS:
            reason = "rejected"
        return ProtocolMessage(
            type=message_type,
            code=code,
            command_type=command_type,
            command_id=command_id,
            reason=reason,
        )

    if message_type == "evt.wifi.status":
        if code != 0:
            raise ProvisioningProtocolError(
                "Wi-Fi status response contains a non-zero or missing code"
            )
        status_value = data.get("status")
        if not isinstance(status_value, str) or status_value not in _WIFI_STATES:
            raise ProvisioningProtocolError(
                "Wi-Fi status response contains an invalid state"
            )
        status = cast(WifiState, status_value)
        return ProtocolMessage(
            type=message_type,
            code=code,
            command_id=command_id,
            status=status,
            ssid=_optional_string(data.get("ssid")),
            ip=_optional_string(data.get("ip")),
        )

    # Request echoes are intentionally reduced to correlation metadata. In
    # particular, password and all other arbitrary request fields are dropped.
    return ProtocolMessage(
        type=message_type,
        code=code,
        command_id=command_id,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class JsonMessageBuffer:
    """Reassemble split or concatenated UTF-8 JSON notifications."""

    def __init__(self) -> None:
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._text = ""
        self._json_decoder = json.JSONDecoder()

    def feed(self, fragment: bytes) -> list[ProtocolMessage]:
        try:
            self._text += self._utf8_decoder.decode(fragment, final=False)
        except UnicodeDecodeError as exc:
            self._reset()
            raise ProvisioningProtocolError(
                "Provisioning notification is not valid UTF-8"
            ) from exc
        if len(self._text) > _MAX_RECEIVE_BUFFER_CHARS:
            self._reset()
            raise ProvisioningProtocolError(
                "Provisioning notification buffer exceeded its limit"
            )

        messages: list[ProtocolMessage] = []
        while True:
            self._text = self._text.lstrip()
            if not self._text:
                break
            if not self._text.startswith("{"):
                newline = self._text.find("\n")
                if newline < 0:
                    break
                # The firmware starts each BLE session in its legacy text
                # mode and can emit one status line as Notify is enabled.
                self._text = self._text[newline + 1 :]
                continue
            try:
                envelope, end = self._json_decoder.raw_decode(self._text)
            except JSONDecodeError:
                break
            self._text = self._text[end:]
            messages.append(_parse_envelope(envelope))
        return messages

    def _reset(self) -> None:
        self._utf8_decoder.reset()
        self._text = ""
