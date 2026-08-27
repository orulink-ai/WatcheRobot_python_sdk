"""Reunite state transitions for the Daemon's pairing slot."""

from __future__ import annotations

import pytest

from watcherobot.runtime.daemon.pairing.protocol import (
    LinkReuniteAccept,
    LinkReuniteRequest,
    PairAccept,
    reunite_response_mac,
)
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
    PairingSessionError,
)

DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
REUNITE_REQUEST_ID = "5256e0a52e79fcccc45eb8e91be4a5fe"
NONCE = "7ba1f4a9dd93cdfca35eebf2fa99e0ff"
BINDING_SECRET = "f8e64ced0ed799c6f1a46a0852fdaf0f80babf6b223da440127c4a8c7a8c03dc"
MANUAL_TOKEN = "c" * 64
REUNITE_TOKEN = "d" * 64
PEER_IP = "192.168.1.23"


def make_session() -> DevicePairingSession:
    return DevicePairingSession(daemon_instance_id=DAEMON_ID)


def start_scan(
    session: DevicePairingSession,
    **overrides,
) -> LinkReuniteRequest:
    values = {
        "request_id": REUNITE_REQUEST_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "websocket_port": 8765,
        "binding_secret": BINDING_SECRET,
        "now": 100.0,
    }
    values.update(overrides)
    return session.start_reunite_scan(**values)


def make_accept(**overrides) -> LinkReuniteAccept:
    values = {
        "request_id": REUNITE_REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "response_mac": reunite_response_mac(
            BINDING_SECRET,
            request_id=REUNITE_REQUEST_ID,
            nonce=NONCE,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
        ),
        "session_token": REUNITE_TOKEN,
    }
    values.update(overrides)
    return LinkReuniteAccept(**values)


def test_scan_occupies_idle_slot_and_exposes_reuniting() -> None:
    session = make_session()

    request = start_scan(session)

    assert session.state is DevicePairingState.DISCOVERING
    assert session.reuniting is True
    assert session.current_request == request
    assert request.daemon_instance_id == DAEMON_ID


def test_scan_requires_idle_slot() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="desktop_link",
        websocket_port=8765,
        now=0.0,
    )

    with pytest.raises(PairingSessionError):
        start_scan(session)


def test_scan_validates_inputs() -> None:
    with pytest.raises(PairingSessionError):
        start_scan(make_session(), nonce="short")
    with pytest.raises(PairingSessionError):
        start_scan(make_session(), binding_secret="nope")
    with pytest.raises(PairingSessionError):
        start_scan(make_session(), websocket_port=0)
    with pytest.raises(PairingSessionError):
        start_scan(make_session(), target_mode="other")


def test_accept_reunite_verifies_mac_and_enters_connecting() -> None:
    session = make_session()
    start_scan(session)

    session.accept_reunite(make_accept(), peer_ip=PEER_IP, now=101.0)

    assert session.state is DevicePairingState.CONNECTING
    assert session.expected_peer_ip == PEER_IP
    assert session.reuniting is False


def test_accept_reunite_rejects_bad_mac_without_state_change() -> None:
    session = make_session()
    start_scan(session)

    with pytest.raises(PairingSessionError):
        session.accept_reunite(make_accept(response_mac="e" * 64), peer_ip=PEER_IP, now=101.0)

    # Still scanning; a correct reply can still succeed.
    assert session.state is DevicePairingState.DISCOVERING
    session.accept_reunite(make_accept(), peer_ip=PEER_IP, now=102.0)
    assert session.state is DevicePairingState.CONNECTING


def test_accept_reunite_binds_reply_to_challenge_fields() -> None:
    session = make_session()
    start_scan(session)

    for mutated in (
        make_accept(nonce="0" * 32),
        make_accept(request_id="9" * 32),
        make_accept(target_mode="python_sdk"),
    ):
        with pytest.raises(PairingSessionError):
            session.accept_reunite(mutated, peer_ip=PEER_IP, now=101.0)


def test_accept_reunite_ignored_outside_scan() -> None:
    session = make_session()
    with pytest.raises(PairingSessionError):
        session.accept_reunite(make_accept(), peer_ip=PEER_IP, now=1.0)


def test_manual_pairing_does_not_consume_slot_of_scan() -> None:
    session = make_session()
    start_scan(session)
    session.accept_reunite(make_accept(), peer_ip=PEER_IP, now=101.0)

    from watcherobot.runtime.daemon.pairing.protocol import HardwareHello

    session.connect_device(
        HardwareHello(
            pair_request_id=REUNITE_REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            session_token=REUNITE_TOKEN,
            mode="desktop_link",
        ),
        peer_ip=PEER_IP,
        now=102.0,
    )
    assert session.state is DevicePairingState.CONNECTED
    # Reunite sessions never reseed the binding secret.
    assert session.take_manual_binding_token() is None


def test_manual_pairing_hands_out_binding_token_once() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=0.0,
    )
    session.accept_device(
        PairAccept(
            request_id=session.current_request.request_id,
            daemon_instance_id=DAEMON_ID,
            target_mode="python_sdk",
            session_token=MANUAL_TOKEN,
        ),
        peer_ip=PEER_IP,
        now=1.0,
    )

    # Not yet connected: nothing to consume.
    assert session.take_manual_binding_token() is None

    from watcherobot.runtime.daemon.pairing.protocol import HardwareHello

    session.connect_device(
        HardwareHello(
            pair_request_id=session.current_request.request_id,
            daemon_instance_id=DAEMON_ID,
            session_token=MANUAL_TOKEN,
            mode="python_sdk",
        ),
        peer_ip=PEER_IP,
        now=2.0,
    )
    assert session.take_manual_binding_token() == MANUAL_TOKEN
    assert session.take_manual_binding_token() is None


def test_disconnect_clears_pending_manual_token() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="desktop_link",
        websocket_port=8765,
        now=0.0,
    )
    session.accept_device(
        PairAccept(
            request_id=session.current_request.request_id,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
            session_token=MANUAL_TOKEN,
        ),
        peer_ip=PEER_IP,
        now=1.0,
    )

    from watcherobot.runtime.daemon.pairing.protocol import HardwareHello

    session.connect_device(
        HardwareHello(
            pair_request_id=session.current_request.request_id,
            daemon_instance_id=DAEMON_ID,
            session_token=MANUAL_TOKEN,
            mode="desktop_link",
        ),
        peer_ip=PEER_IP,
        now=2.0,
    )
    session.device_disconnected(now=3.0)

    assert session.take_manual_binding_token() is None


def test_expired_scan_reports_reunite_unavailable() -> None:
    session = make_session()
    start_scan(session)

    expired = session.expire(now=100.0 + session._discovery_timeout_seconds + 1)

    assert expired is True
    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["last_error"] == "reunite_unavailable"


def test_cancel_clears_reunite_flags() -> None:
    session = make_session()
    start_scan(session)

    assert session.cancel() is True

    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["last_error"] == "pairing_cancelled"
