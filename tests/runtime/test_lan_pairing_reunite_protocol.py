"""Golden-vector tests for the watcher-lan-pairing/1.1 reunite datagrams.

The vectors mirror docs/lan-fast-reconnect-protocol-design.md so the ESP32
host contract tests, this suite and the desktop tooling all prove the same
bytes.
"""

from __future__ import annotations

import json

import pytest

from watcherobot.runtime.daemon.pairing.protocol import (
    LAN_PAIRING_PROTOCOL,
    LAN_PAIRING_REUNITE_VERSION,
    LinkReuniteAccept,
    LinkReuniteRequest,
    PairingProtocolError,
    derive_binding_secret,
    encode_udp_message,
    parse_hardware_hello,
    parse_udp_message,
    reunite_response_mac,
)

DAEMON_ID = "ddddddddddddddddddddddddddddddddd"[:32]
REQUEST_ID = "5256e0a52e79fcccc45eb8e91be4a5fe"
NONCE = "7ba1f4a9dd93cdfca35eebf2fa99e0ff"
SESSION_TOKEN = (
    "9f86d081884c7d659a2feaa0c55ad015"
    "a3bf4f1b2b0b822cd15d6c15b0f00a08"
)
BINDING_SECRET = "f8e64ced0ed799c6f1a46a0852fdaf0f80babf6b223da440127c4a8c7a8c03dc"
RESPONSE_MAC = "d6a733209586c8f7e628b352885ed121ebf4673e9fa727899e4c74b13c586f30"


def make_request(**overrides) -> LinkReuniteRequest:
    values = {
        "request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "websocket_port": 8765,
    }
    values.update(overrides)
    return LinkReuniteRequest(**values)


def make_accept(**overrides) -> LinkReuniteAccept:
    values = {
        "request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "response_mac": RESPONSE_MAC,
        "session_token": SESSION_TOKEN,
    }
    values.update(overrides)
    return LinkReuniteAccept(**values)


def test_golden_binding_secret_derivation() -> None:
    assert derive_binding_secret(SESSION_TOKEN) == BINDING_SECRET


def test_golden_response_mac() -> None:
    mac = reunite_response_mac(
        BINDING_SECRET,
        request_id=REQUEST_ID,
        nonce=NONCE,
        daemon_instance_id=DAEMON_ID,
        target_mode="desktop_link",
    )
    assert mac == RESPONSE_MAC


def test_reunite_request_roundtrip_preserves_version_11() -> None:
    encoded = encode_udp_message(make_request())
    payload = json.loads(encoded.decode("utf-8"))
    assert payload["type"] == "link.reunite.request"
    assert payload["version"] == LAN_PAIRING_REUNITE_VERSION
    parsed = parse_udp_message(encoded)
    assert isinstance(parsed, LinkReuniteRequest)
    assert parsed == make_request()


def test_reunite_accept_roundtrip() -> None:
    encoded = encode_udp_message(make_accept())
    parsed = parse_udp_message(encoded)
    assert isinstance(parsed, LinkReuniteAccept)
    assert parsed == make_accept()


def test_reunite_fields_are_exactly_matched() -> None:
    base = json.loads(encode_udp_message(make_request()).decode("utf-8"))
    for mutated in (
        {**base, "extra": 1},
        {key: value for key, value in base.items() if key != "nonce"},
    ):
        with pytest.raises(PairingProtocolError):
            parse_udp_message(json.dumps(mutated))


def test_reunite_rejects_legacy_version_and_vice_versa() -> None:
    request_payload = {
        "type": "link.reunite.request",
        "protocol": LAN_PAIRING_PROTOCOL,
        "version": "1.0",
        "request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "websocket_port": 8765,
    }
    with pytest.raises(PairingProtocolError):
        parse_udp_message(request_payload)

    pair_payload = {
        "type": "pair.request",
        "protocol": LAN_PAIRING_PROTOCOL,
        "version": LAN_PAIRING_REUNITE_VERSION,
        "request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "pairing_code": "123456",
        "target_mode": "desktop_link",
        "websocket_port": 8765,
    }
    with pytest.raises(PairingProtocolError):
        parse_udp_message(pair_payload)


def test_reunite_validates_formats() -> None:
    with pytest.raises(PairingProtocolError):
        parse_udp_message(encode_udp_message(make_request(nonce="xyz")))
    with pytest.raises(PairingProtocolError):
        parse_udp_message(encode_udp_message(make_request(websocket_port=0)))
    with pytest.raises(PairingProtocolError):
        parse_udp_message(encode_udp_message(make_accept(response_mac="AA" * 32)))
    with pytest.raises(PairingProtocolError):
        parse_udp_message(encode_udp_message(make_request(target_mode="other")))


def test_mac_mismatch_is_detectable_by_caller() -> None:
    verified = reunite_response_mac(
        BINDING_SECRET,
        request_id=REQUEST_ID,
        nonce=NONCE,
        daemon_instance_id=DAEMON_ID,
        target_mode="desktop_link",
    )
    tampered = make_accept(nonce="0" * 32)
    expected_for_tampered = reunite_response_mac(
        BINDING_SECRET,
        request_id=tampered.request_id,
        nonce=tampered.nonce,
        daemon_instance_id=tampered.daemon_instance_id,
        target_mode=tampered.target_mode,
    )
    assert tampered.response_mac != expected_for_tampered
    assert verified != expected_for_tampered


def _hello_frame(pairing_version: str) -> bytes:
    return json.dumps(
        {
            "type": "sys.client.hello",
            "code": 0,
            "data": {
                "role": "hardware",
                "pairing_protocol": LAN_PAIRING_PROTOCOL,
                "pairing_version": pairing_version,
                "pair_request_id": REQUEST_ID,
                "daemon_instance_id": DAEMON_ID,
                "session_token": SESSION_TOKEN,
                "mode": "desktop_link",
            },
        }
    ).encode("utf-8")


@pytest.mark.parametrize("pairing_version", ["1.0", "1.1"])
def test_hello_accepts_both_protocol_generations(pairing_version: str) -> None:
    hello = parse_hardware_hello(_hello_frame(pairing_version))
    assert hello.mode == "desktop_link"


def test_hello_rejects_unknown_protocol_generation() -> None:
    with pytest.raises(PairingProtocolError):
        parse_hardware_hello(_hello_frame("2.0"))
