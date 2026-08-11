from __future__ import annotations

import pytest

from watcherobot.runtime.daemon.pairing.protocol import HardwareHello, PairAccept, PairBusy
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
    PairingSessionError,
)


DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)


def make_session() -> DevicePairingSession:
    return DevicePairingSession(
        daemon_instance_id=DAEMON_ID,
        request_id_factory=lambda: REQUEST_ID,
    )


def make_accept(**overrides) -> PairAccept:
    values = {
        "request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "target_mode": "python_sdk",
        "session_token": SESSION_TOKEN,
    }
    values.update(overrides)
    return PairAccept(**values)


def make_hello(**overrides) -> HardwareHello:
    values = {
        "pair_request_id": REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "session_token": SESSION_TOKEN,
        "mode": "python_sdk",
    }
    values.update(overrides)
    return HardwareHello(**values)


def test_pairing_session_starts_idle_without_public_credentials() -> None:
    session = make_session()

    assert session.state is DevicePairingState.IDLE
    assert session.snapshot() == {
        "state": "idle",
        "online": False,
        "mode": None,
        "request_id": None,
        "last_error": None,
    }


def test_start_pairing_occupies_the_only_device_slot() -> None:
    session = make_session()

    request = session.start_pairing(
        pairing_code="000000",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )

    assert request.request_id == REQUEST_ID
    assert request.pairing_code == "000000"
    assert session.state is DevicePairingState.DISCOVERING
    assert session.snapshot()["online"] is False
    with pytest.raises(PairingSessionError) as error:
        session.start_pairing(
            pairing_code="999999",
            target_mode="python_sdk",
            websocket_port=8765,
            now=11.0,
        )
    assert error.value.code == "device_slot_occupied"


@pytest.mark.parametrize("target_mode", ["desktop_link", "python_sdk"])
def test_start_pairing_accepts_each_supported_application_mode(
    target_mode: str,
) -> None:
    session = make_session()

    request = session.start_pairing(
        pairing_code="123456",
        target_mode=target_mode,
        websocket_port=8765,
        now=10.0,
    )

    assert request.target_mode == target_mode


@pytest.mark.parametrize(
    ("pairing_code", "target_mode", "expected_code"),
    [
        ("12345", "python_sdk", "invalid_pairing_code"),
        ("123456", "sdk", "unsupported_target_mode"),
    ],
)
def test_start_pairing_rejects_invalid_entry_values(
    pairing_code,
    target_mode,
    expected_code,
) -> None:
    session = make_session()

    with pytest.raises(PairingSessionError) as error:
        session.start_pairing(
            pairing_code=pairing_code,
            target_mode=target_mode,
            websocket_port=8765,
            now=10.0,
        )

    assert error.value.code == expected_code
    assert session.state is DevicePairingState.IDLE


def test_accept_moves_to_connecting_and_never_exposes_secrets() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )

    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)

    assert session.state is DevicePairingState.CONNECTING
    assert session.expected_peer_ip == "192.168.3.25"
    snapshot_text = repr(session.snapshot())
    assert "123456" not in snapshot_text
    assert SESSION_TOKEN not in snapshot_text


def test_hardware_hello_is_the_only_transition_to_online() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)

    session.connect_device(make_hello(), peer_ip="192.168.3.25", now=14.0)

    assert session.state is DevicePairingState.CONNECTED
    assert session.snapshot()["online"] is True


@pytest.mark.parametrize(
    ("hello", "peer_ip"),
    [
        (make_hello(session_token="0" * 64), "192.168.3.25"),
        (make_hello(daemon_instance_id="0" * 32), "192.168.3.25"),
        (make_hello(pair_request_id="0" * 32), "192.168.3.25"),
        (make_hello(), "192.168.3.99"),
    ],
)
def test_hardware_hello_rejects_wrong_session_credentials(hello, peer_ip) -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)

    with pytest.raises(PairingSessionError) as error:
        session.connect_device(hello, peer_ip=peer_ip, now=14.0)

    assert error.value.code == "pairing_credential_invalid"
    assert session.state is DevicePairingState.CONNECTING
    assert session.snapshot()["online"] is False


def test_abnormal_disconnect_reserves_slot_for_same_session_reconnect() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)
    session.connect_device(make_hello(), peer_ip="192.168.3.25", now=14.0)

    session.device_disconnected(now=20.0)

    assert session.state is DevicePairingState.RECONNECTING
    assert session.snapshot()["online"] is False
    with pytest.raises(PairingSessionError) as error:
        session.start_pairing(
            pairing_code="999999",
            target_mode="python_sdk",
            websocket_port=8765,
            now=21.0,
        )
    assert error.value.code == "device_slot_occupied"

    session.connect_device(make_hello(), peer_ip="192.168.3.25", now=25.0)
    assert session.state is DevicePairingState.CONNECTED
    assert session.snapshot()["online"] is True


@pytest.mark.parametrize(
    ("advance", "expected_error"),
    [
        (("discovering", 20.0), "pairing_not_found"),
        (("connecting", 23.0), "device_connect_timeout"),
        (("reconnecting", 51.0), "reconnect_timeout"),
    ],
)
def test_timeouts_release_the_device_slot(advance, expected_error) -> None:
    phase, now = advance
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    if phase in {"connecting", "reconnecting"}:
        session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)
    if phase == "reconnecting":
        session.connect_device(make_hello(), peer_ip="192.168.3.25", now=14.0)
        session.device_disconnected(now=20.0)

    assert session.expire(now=now) is True
    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["last_error"] == expected_error


def test_busy_cancel_and_explicit_release_return_to_idle() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.reject_busy(
        PairBusy(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            reason="device_session_active",
        )
    )
    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["last_error"] == "device_busy"

    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=20.0,
    )
    assert session.cancel() is True
    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["last_error"] == "pairing_cancelled"

    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=30.0,
    )
    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=31.0)
    session.connect_device(make_hello(), peer_ip="192.168.3.25", now=32.0)
    session.release()
    assert session.snapshot() == {
        "state": "idle",
        "online": False,
        "mode": None,
        "request_id": None,
        "last_error": None,
    }


def test_device_session_end_releases_only_the_current_connected_session() -> None:
    session = make_session()
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.accept_device(make_accept(), peer_ip="192.168.3.25", now=12.0)
    session.connect_device(make_hello(), peer_ip="192.168.3.25", now=14.0)

    with pytest.raises(PairingSessionError) as error:
        session.end_device_session(pair_request_id="0" * 32)
    assert error.value.code == "pairing_credential_invalid"
    assert session.state is DevicePairingState.CONNECTED

    session.end_device_session(pair_request_id=REQUEST_ID)
    assert session.state is DevicePairingState.IDLE
    assert session.snapshot()["request_id"] is None
