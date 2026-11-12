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
    LinkReuniteAvailable,
    LinkReuniteRequest,
    PairingProtocolError,
    account_reunite_mac,
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


def test_account_reunite_fields_are_required_together() -> None:
    base = json.loads(encode_udp_message(make_request()))
    target = "WR-0001-0023-4567-89AB"
    mac = "42" * 32
    fields = {"device_id": target, "request_mac": mac, "daemon_ip": "192.168.31.99"}
    parsed = parse_udp_message({**base, **fields})
    assert isinstance(parsed, LinkReuniteRequest)
    assert json.loads(encode_udp_message(parsed)) == {
        **base, **fields,
    }
    for invalid_fields in (
        {key: value for key, value in fields.items() if key != "device_id"},
        {key: value for key, value in fields.items() if key != "request_mac"},
        {key: value for key, value in fields.items() if key != "daemon_ip"},
        {**fields, "device_id": target.lower()},
        {**fields, "request_mac": "A" * 64},
        {**fields, "device_id": None},
        {**fields, "daemon_ip": "192.168.31.099"},
        {**fields, "daemon_ip": "::1"},
        {**fields, "target_mode": "python_sdk"},
    ):
        with pytest.raises(PairingProtocolError):
            parse_udp_message({**base, **invalid_fields})


def test_account_reunite_golden_vector_matches_firmware() -> None:
    # Same fixed input and results as ESP32 test_account_reunite.
    request = make_request(device_id="WR-0123-4567-89AB-CDEF", daemon_ip="192.168.31.99")
    link_key = bytes(range(32)).hex()
    assert account_reunite_mac(link_key, request, purpose="account-request") == (
        "46697f90a775770381a082a1b0df135839d3cec60d2e996db32eff6506821f88"
    )
    assert account_reunite_mac(link_key, request, purpose="account-accept") == (
        "8b13961541ad557827ffc1ef42e38a18bb4d0fcae74337a48c0f8b14a9312298"
    )
    assert account_reunite_mac(link_key, request, purpose="account-session") == (
        "c9e6434a02ea21bac98052243d48d56c162253a6da6b6d1a0c213886f0976303"
    )
    assert reunite_response_mac(
        link_key,
        request_id=request.request_id,
        nonce=request.nonce,
        daemon_instance_id=request.daemon_instance_id,
        target_mode=request.target_mode,
    ) == "ba978bfd39c03e3e435108b45da2a3ac098da0018c4ea4bd7f21d03823df5968"


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


@pytest.mark.parametrize("overrides", [
    {"version": "1.0"}, {"protocol": "other"},
    {"daemon_instance_id": "invalid"}, {"target_mode": "invalid"},
    {"request_id": REQUEST_ID}, {"binding_secret": BINDING_SECRET},
])
def test_availability_rejects_invalid_contract(overrides) -> None:
    payload = {
        "type": "link.reunite.available", "protocol": LAN_PAIRING_PROTOCOL,
        "version": "1.1", "daemon_instance_id": DAEMON_ID,
        "target_mode": "desktop_link", **overrides,
    }
    with pytest.raises(PairingProtocolError):
        parse_udp_message(json.dumps(payload).encode())


def test_availability_parses_without_request_or_credentials() -> None:
    payload = {
        "type": "link.reunite.available", "protocol": LAN_PAIRING_PROTOCOL,
        "version": "1.1", "daemon_instance_id": DAEMON_ID,
        "target_mode": "desktop_link",
    }
    assert parse_udp_message(json.dumps(payload).encode()) == LinkReuniteAvailable(DAEMON_ID, "desktop_link")
