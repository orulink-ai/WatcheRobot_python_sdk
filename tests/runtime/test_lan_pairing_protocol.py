from __future__ import annotations

import json
from pathlib import Path

import pytest

from watcherobot.runtime.daemon.pairing.protocol import (
    LAN_PAIRING_PROTOCOL,
    LAN_PAIRING_VERSION,
    DeviceSessionEnd,
    HardwareHello,
    MediaHello,
    PairAccept,
    PairBusy,
    PairCancel,
    PairRequest,
    PairingProtocolError,
    build_device_state_event,
    build_hardware_hello_ack,
    build_hardware_hello_nack,
    build_media_hello_ack,
    encode_udp_message,
    parse_hardware_hello,
    parse_media_hello,
    parse_device_session_end,
    parse_udp_message,
    redact_sensitive_fields,
)


VECTORS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "contracts"
    / "watcher_lan_pairing_v1.json"
)


@pytest.fixture(scope="module")
def vectors() -> dict[str, object]:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def test_protocol_vectors_fix_name_version_and_message_shapes(vectors) -> None:
    assert vectors["protocol"] == LAN_PAIRING_PROTOCOL
    assert vectors["version"] == LAN_PAIRING_VERSION
    valid = vectors["valid"]

    assert isinstance(parse_udp_message(valid["pair_request"]), PairRequest)
    assert isinstance(parse_udp_message(valid["pair_accept"]), PairAccept)
    assert isinstance(parse_udp_message(valid["pair_busy"]), PairBusy)
    assert isinstance(parse_udp_message(valid["pair_cancel"]), PairCancel)
    assert isinstance(parse_hardware_hello(valid["hardware_hello"]), HardwareHello)
    assert isinstance(parse_media_hello(valid["media_hello"]), MediaHello)
    assert isinstance(parse_device_session_end(valid["session_end"]), DeviceSessionEnd)

    hello_data = valid["hardware_hello"]["data"]
    assert "device_id" not in hello_data
    assert "mac" not in hello_data
    assert "capabilities" not in hello_data
    assert "pairing_code" not in hello_data
    assert valid["hello_ack"]["data"]["negotiated"]["audio_uplink"]["codec"] == "opus"
    assert valid["hello_ack"]["data"]["negotiated"]["video_uplink"] == {
        "transport": "websocket_sidecar",
        "hello_type": "sys.media.hello",
        "version": 1,
    }


@pytest.mark.parametrize("pairing_code", ["000000", "999999"])
def test_pair_request_accepts_six_digit_boundaries(vectors, pairing_code) -> None:
    payload = dict(vectors["valid"]["pair_request"])
    payload["pairing_code"] = pairing_code
    assert parse_udp_message(payload).pairing_code == pairing_code


def test_invalid_contract_values_are_rejected(vectors) -> None:
    valid_request = vectors["valid"]["pair_request"]
    invalid = vectors["invalid"]

    for pairing_code in invalid["pairing_codes"]:
        with pytest.raises(PairingProtocolError, match="pairing_code"):
            parse_udp_message({**valid_request, "pairing_code": pairing_code})
    for request_id in invalid["request_ids"]:
        with pytest.raises(PairingProtocolError, match="request_id"):
            parse_udp_message({**valid_request, "request_id": request_id})
    for version in invalid["protocol_versions"]:
        with pytest.raises(PairingProtocolError, match="version"):
            parse_udp_message({**valid_request, "version": version})
    for mode in invalid["target_modes"]:
        with pytest.raises(PairingProtocolError, match="target_mode"):
            parse_udp_message({**valid_request, "target_mode": mode})
    for websocket_port in invalid["websocket_ports"]:
        with pytest.raises(PairingProtocolError, match="websocket_port"):
            parse_udp_message({**valid_request, "websocket_port": websocket_port})

    valid_accept = vectors["valid"]["pair_accept"]
    for session_token in invalid["session_tokens"]:
        with pytest.raises(PairingProtocolError, match="session_token"):
            parse_udp_message({**valid_accept, "session_token": session_token})


def test_unknown_and_legacy_fields_are_rejected(vectors) -> None:
    request = dict(vectors["valid"]["pair_request"])
    request["device_id"] = "legacy-device"
    with pytest.raises(PairingProtocolError, match="unknown fields"):
        parse_udp_message(request)

    hello = {
        **vectors["valid"]["hardware_hello"],
        "data": {
            **vectors["valid"]["hardware_hello"]["data"],
            "capabilities": {"audio": True},
        },
    }
    with pytest.raises(PairingProtocolError, match="unknown fields"):
        parse_hardware_hello(hello)

    session_end = {
        **vectors["valid"]["session_end"],
        "data": {
            **vectors["valid"]["session_end"]["data"],
            "device_id": "legacy-device",
        },
    }
    with pytest.raises(PairingProtocolError, match="unknown fields"):
        parse_device_session_end(session_end)


def test_session_end_requires_current_request_shape(vectors) -> None:
    parsed = parse_device_session_end(vectors["valid"]["session_end"])
    assert parsed.pair_request_id == vectors["valid"]["pair_request"]["request_id"]
    assert parsed.reason == "mode_exit"

    invalid_reason = {
        **vectors["valid"]["session_end"],
        "data": {
            **vectors["valid"]["session_end"]["data"],
            "reason": "unexpected",
        },
    }
    with pytest.raises(PairingProtocolError, match="reason"):
        parse_device_session_end(invalid_reason)


def test_sensitive_fields_are_redacted_without_mutating_source(vectors) -> None:
    source = {
        "pairing_code": "123456",
        "data": {
            "session_token": vectors["valid"]["pair_accept"]["session_token"],
            "request_id": vectors["valid"]["pair_accept"]["request_id"],
        },
    }

    redacted = redact_sensitive_fields(source)

    assert redacted["pairing_code"] == "[redacted]"
    assert redacted["data"]["session_token"] == "[redacted:ae4b]"
    assert redacted["data"]["request_id"] == source["data"]["request_id"]
    assert source["pairing_code"] == "123456"


def test_udp_encoder_matches_the_shared_wire_vectors(vectors) -> None:
    for vector_name in ("pair_request", "pair_accept", "pair_busy", "pair_cancel"):
        parsed = parse_udp_message(vectors["valid"][vector_name])
        assert json.loads(encode_udp_message(parsed)) == vectors["valid"][vector_name]


def test_hardware_ack_nack_and_state_event_have_stable_envelopes(vectors) -> None:
    assert build_hardware_hello_ack() == vectors["valid"]["hello_ack"]
    assert build_hardware_hello_nack(
        code=401,
        error="pairing_credential_invalid",
    ) == {
        "type": "sys.nack",
        "code": 401,
        "data": {
            "type": "sys.client.hello",
            "error": "pairing_credential_invalid",
        },
    }
    assert build_device_state_event(
        vectors["valid"]["device_state"]["data"],
    ) == vectors["valid"]["device_state"]
    assert build_media_hello_ack() == vectors["valid"]["media_hello_ack"]


def test_media_hello_rejects_wrong_channel_version_and_unknown_fields(vectors) -> None:
    valid = vectors["valid"]["media_hello"]
    for field, value in (("channel", "audio"), ("version", 2)):
        payload = {**valid, "data": {**valid["data"], field: value}}
        with pytest.raises(PairingProtocolError, match=field):
            parse_media_hello(payload)

    payload = {**valid, "data": {**valid["data"], "device_id": "legacy"}}
    with pytest.raises(PairingProtocolError, match="unknown fields"):
        parse_media_hello(payload)
