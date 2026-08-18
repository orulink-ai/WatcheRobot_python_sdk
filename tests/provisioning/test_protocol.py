from __future__ import annotations

import json

import pytest

from watcherobot.provisioning.errors import (
    PayloadTooLargeError,
    ProvisioningProtocolError,
)
from watcherobot.provisioning.protocol import (
    BLE_CHARACTERISTIC_UUID,
    BLE_SERVICE_UUID,
    MAX_PROTOCOL_PAYLOAD_BYTES,
    JsonMessageBuffer,
    build_request,
    parse_message,
)


def test_protocol_constants_match_the_shipped_firmware() -> None:
    assert BLE_SERVICE_UUID == "000000ff-0000-1000-8000-00805f9b34fb"
    assert BLE_CHARACTERISTIC_UUID == "0000ff01-0000-1000-8000-00805f9b34fb"
    assert MAX_PROTOCOL_PAYLOAD_BYTES == 180


def test_wifi_request_is_compact_utf8_and_preserves_non_ascii_ssid() -> None:
    payload = build_request(
        "cfg.wifi.set",
        {
            "ssid": "测试网络",
            "password": "secret",
            "command_id": "python-wifi-set-1",
        },
    )

    assert b"\\u" not in payload
    assert json.loads(payload.decode("utf-8")) == {
        "type": "cfg.wifi.set",
        "data": {
            "ssid": "测试网络",
            "password": "secret",
            "command_id": "python-wifi-set-1",
        },
    }


def test_payload_limit_error_does_not_expose_password() -> None:
    password = "secret-" + ("x" * MAX_PROTOCOL_PAYLOAD_BYTES)

    with pytest.raises(PayloadTooLargeError) as captured:
        build_request(
            "cfg.wifi.set",
            {
                "ssid": "Office",
                "password": password,
                "command_id": "python-wifi-set-2",
            },
        )

    assert password not in str(captured.value)
    assert "180" in str(captured.value)


def test_request_echo_is_sanitized_during_parsing() -> None:
    message = parse_message(
        '{"type":"cfg.wifi.set","data":{"ssid":"Office",'
        '"password":"do-not-retain","command_id":"cmd-1"}}'
    )

    assert message.type == "cfg.wifi.set"
    assert message.command_id == "cmd-1"
    assert "do-not-retain" not in repr(message)
    assert "do-not-retain" not in json.dumps(message.to_dict())


def test_unrecognized_nack_reason_is_not_retained() -> None:
    message = parse_message(
        '{"type":"sys.nack","code":400,"data":'
        '{"type":"cfg.wifi.set","command_id":"cmd-1",'
        '"reason":"do-not-retain"}}'
    )

    assert message.reason == "rejected"
    assert "do-not-retain" not in repr(message)


def test_json_buffer_reassembles_utf8_fragments_and_concatenated_messages() -> None:
    first = json.dumps(
        {
            "type": "sys.ack",
            "code": 0,
            "data": {
                "type": "cfg.wifi.set",
                "command_id": "命令-1",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    second = (
        b'{"type":"evt.wifi.status","code":0,'
        b'"data":{"status":"connected","ssid":"Office"}}'
    )
    split_at = first.index("命".encode("utf-8")) + 1
    decoder = JsonMessageBuffer()

    assert decoder.feed(first[:split_at]) == []
    messages = decoder.feed(first[split_at:] + second)

    assert [message.type for message in messages] == [
        "sys.ack",
        "evt.wifi.status",
    ]
    assert messages[0].command_id == "命令-1"
    assert messages[1].status == "connected"


def test_json_buffer_rejects_invalid_utf8_without_echoing_secret_bytes() -> None:
    decoder = JsonMessageBuffer()

    with pytest.raises(ProvisioningProtocolError) as captured:
        decoder.feed(b"\xffsecret-password")

    assert "secret-password" not in str(captured.value)


def test_json_buffer_ignores_firmware_legacy_status_before_json() -> None:
    decoder = JsonMessageBuffer()

    assert decoder.feed(b"WIFI_CONNECTED:Office:192.168.") == []
    messages = decoder.feed(
        b'1.9\n{"type":"evt.wifi.status","code":0,'
        b'"data":{"status":"connected","ssid":"Office"}}'
    )

    assert len(messages) == 1
    assert messages[0].status == "connected"


def test_parse_message_validates_nack_and_wifi_status() -> None:
    nack = parse_message(
        '{"type":"sys.nack","code":400,"data":'
        '{"type":"cfg.wifi.set","command_id":"cmd-1",'
        '"reason":"invalid_wifi_payload"}}'
    )
    status = parse_message(
        '{"type":"evt.wifi.status","code":0,"data":'
        '{"status":"unconfigured"}}'
    )

    assert nack.reason == "invalid_wifi_payload"
    assert nack.command_type == "cfg.wifi.set"
    assert status.status == "unconfigured"


@pytest.mark.parametrize(
    "state",
    ["auth_failed", "network_not_found", "timeout"],
)
def test_parse_message_accepts_terminal_wifi_failure_states(
    state: str,
) -> None:
    message = parse_message(
        '{"type":"evt.wifi.status","code":0,"data":'
        f'{{"status":"{state}","ssid":"Office",'
        '"command_id":"cmd-1"}}'
    )

    assert message.status == state
    assert message.ssid == "Office"
    assert message.command_id == "cmd-1"


def test_success_ack_does_not_report_a_rejection_reason() -> None:
    ack = parse_message(
        '{"type":"sys.ack","code":0,"data":'
        '{"type":"cfg.wifi.set","command_id":"cmd-1"}}'
    )

    assert ack.reason is None
    assert "reason" not in ack.to_dict()


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"type":"","data":{}}',
        '{"type":"sys.ack","code":1,"data":{"type":"cfg.wifi.set"}}',
        '{"type":"evt.wifi.status","code":0,"data":{"status":"unknown"}}',
        '{"type":"evt.wifi.status","data":{"status":"connected"}}',
    ],
)
def test_parse_message_rejects_invalid_envelopes(raw: str) -> None:
    with pytest.raises(ProvisioningProtocolError):
        parse_message(raw)
