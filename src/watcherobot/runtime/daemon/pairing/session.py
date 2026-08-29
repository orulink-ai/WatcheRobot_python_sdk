"""In-memory authority for the Daemon's single device pairing slot."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from enum import Enum

from watcherobot.runtime.daemon.pairing.protocol import (
    LAN_PAIRING_TARGET_MODES,
    HardwareHello,
    LinkReuniteAccept,
    LinkReuniteRequest,
    PairAccept,
    PairBusy,
    PairCancel,
    PairRequest,
    reunite_response_mac,
)


DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 10.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_RECONNECT_TIMEOUT_SECONDS = 30.0

_LOWER_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_PAIRING_CODE = re.compile(r"^[0-9]{6}$")


class DevicePairingState(str, Enum):
    IDLE = "idle"
    DISCOVERING = "discovering"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class PairingSessionError(RuntimeError):
    """Stable error reported by the pairing state authority."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class DevicePairingSession:
    """Own one in-memory pairing session and exactly one device slot."""

    def __init__(
        self,
        *,
        daemon_instance_id: str,
        request_id_factory: Callable[[], str] | None = None,
        discovery_timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        reconnect_timeout_seconds: float = DEFAULT_RECONNECT_TIMEOUT_SECONDS,
    ) -> None:
        if _LOWER_HEX_32.fullmatch(daemon_instance_id) is None:
            raise ValueError("daemon_instance_id must be 32 lowercase hex characters")
        if (
            discovery_timeout_seconds <= 0
            or connect_timeout_seconds <= 0
            or reconnect_timeout_seconds <= 0
        ):
            raise ValueError("pairing timeouts must be positive")

        self.daemon_instance_id = daemon_instance_id
        self._request_id_factory = request_id_factory or (lambda: secrets.token_hex(16))
        self._discovery_timeout_seconds = discovery_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._reconnect_timeout_seconds = reconnect_timeout_seconds

        self._state = DevicePairingState.IDLE
        self._request: PairRequest | LinkReuniteRequest | None = None
        self._session_token: str | None = None
        self._expected_peer_ip: str | None = None
        self._deadline: float | None = None
        self._last_error: str | None = None
        # watcher-lan-pairing/1.1 fast reconnect bookkeeping.
        self._reuniting = False
        self._reunite_secret: str | None = None
        self._pending_manual_token: str | None = None

    @property
    def state(self) -> DevicePairingState:
        return self._state

    @property
    def reuniting(self) -> bool:
        """True while an automatic reunite scan owns the slot."""

        return self._reuniting and self._state is DevicePairingState.DISCOVERING

    @property
    def current_request(self) -> PairRequest | LinkReuniteRequest | None:
        return self._request

    @property
    def expected_peer_ip(self) -> str | None:
        return self._expected_peer_ip

    def preview_transport_credentials(self) -> tuple[str, bytes] | None:
        """Return ephemeral media credentials only while the device is online."""

        if (
            self._state is not DevicePairingState.CONNECTED
            or self._expected_peer_ip is None
            or self._session_token is None
        ):
            return None
        key = hmac.new(
            self._session_token.encode("ascii"),
            b"face-preview-v1",
            hashlib.sha256,
        ).digest()
        return self._expected_peer_ip, key

    def snapshot(self) -> dict[str, object]:
        """Return only public state; connection code and token never escape."""

        return {
            "state": self._state.value,
            "online": self._state is DevicePairingState.CONNECTED,
            "mode": self._request.target_mode if self._request is not None else None,
            "request_id": (
                self._request.request_id if self._request is not None else None
            ),
            "last_error": self._last_error,
        }

    def start_pairing(
        self,
        *,
        pairing_code: str,
        target_mode: str,
        websocket_port: int,
        now: float,
    ) -> PairRequest:
        if self._state is not DevicePairingState.IDLE:
            raise PairingSessionError("device_slot_occupied")
        if _PAIRING_CODE.fullmatch(pairing_code or "") is None:
            raise PairingSessionError("invalid_pairing_code")
        if target_mode not in LAN_PAIRING_TARGET_MODES:
            raise PairingSessionError("unsupported_target_mode")
        if type(websocket_port) is not int or not 1 <= websocket_port <= 65535:
            raise PairingSessionError("invalid_websocket_port")

        request_id = self._request_id_factory()
        if _LOWER_HEX_32.fullmatch(request_id) is None:
            raise PairingSessionError(
                "internal_error",
                "request id factory returned an invalid value",
            )

        self._request = PairRequest(
            request_id=request_id,
            daemon_instance_id=self.daemon_instance_id,
            pairing_code=pairing_code,
            target_mode=target_mode,
            websocket_port=websocket_port,
        )
        self._session_token = None
        self._expected_peer_ip = None
        self._state = DevicePairingState.DISCOVERING
        self._deadline = now + self._discovery_timeout_seconds
        self._last_error = None
        return self._request

    def accept_device(
        self,
        response: PairAccept,
        *,
        peer_ip: str,
        now: float,
    ) -> None:
        if self._state is not DevicePairingState.DISCOVERING:
            raise PairingSessionError("invalid_state_transition")
        assert self._request is not None
        if (
            not self._matches_response(
                request_id=response.request_id,
                daemon_instance_id=response.daemon_instance_id,
            )
            or response.target_mode != self._request.target_mode
        ):
            raise PairingSessionError("pairing_credential_invalid")
        if not peer_ip:
            raise PairingSessionError("pairing_credential_invalid")

        self._session_token = response.session_token
        # Manual pairing is the only exchange allowed to (re)seed the
        # long-term binding secret; reunite sessions keep it stable.
        self._pending_manual_token = response.session_token
        self._expected_peer_ip = peer_ip
        self._state = DevicePairingState.CONNECTING
        self._deadline = now + self._connect_timeout_seconds

    def reject_busy(self, response: PairBusy) -> None:
        if self._state is not DevicePairingState.DISCOVERING:
            raise PairingSessionError("invalid_state_transition")
        if not self._matches_response(
            request_id=response.request_id,
            daemon_instance_id=response.daemon_instance_id,
        ):
            raise PairingSessionError("pairing_credential_invalid")
        self._reset(last_error="device_busy")

    def connect_device(
        self,
        hello: HardwareHello,
        *,
        peer_ip: str,
        now: float,
    ) -> None:
        if self._state not in {
            DevicePairingState.CONNECTING,
            DevicePairingState.RECONNECTING,
        }:
            raise PairingSessionError("pairing_session_required")
        assert self._request is not None

        credentials_match = (
            hello.pair_request_id == self._request.request_id
            and hello.daemon_instance_id == self.daemon_instance_id
            and hello.session_token == self._session_token
            and hello.mode == self._request.target_mode
            and peer_ip == self._expected_peer_ip
        )
        if not credentials_match:
            raise PairingSessionError("pairing_credential_invalid")

        self._state = DevicePairingState.CONNECTED
        self._deadline = None
        self._last_error = None

    def start_reunite_scan(
        self,
        *,
        request_id: str,
        nonce: str,
        target_mode: str,
        websocket_port: int,
        binding_secret: str,
        now: float,
    ) -> LinkReuniteRequest:
        """Occupy the idle slot with an automatic reunite broadcast scan."""

        if self._state is not DevicePairingState.IDLE:
            raise PairingSessionError("device_slot_occupied")
        if target_mode not in LAN_PAIRING_TARGET_MODES:
            raise PairingSessionError("unsupported_target_mode")
        if type(websocket_port) is not int or not 1 <= websocket_port <= 65535:
            raise PairingSessionError("invalid_websocket_port")
        if (
            _LOWER_HEX_32.fullmatch(request_id or "") is None
            or _LOWER_HEX_32.fullmatch(nonce or "") is None
            or _LOWER_HEX_64.fullmatch(binding_secret or "") is None
        ):
            raise PairingSessionError("internal_error", "invalid reunite credentials")

        self._request = LinkReuniteRequest(
            request_id=request_id,
            daemon_instance_id=self.daemon_instance_id,
            nonce=nonce,
            target_mode=target_mode,
            websocket_port=websocket_port,
        )
        self._reuniting = True
        self._reunite_secret = binding_secret
        self._session_token = None
        self._expected_peer_ip = None
        self._state = DevicePairingState.DISCOVERING
        self._deadline = now + self._discovery_timeout_seconds
        self._last_error = None
        return self._request

    def accept_reunite(
        self,
        response: LinkReuniteAccept,
        *,
        peer_ip: str,
        now: float,
    ) -> None:
        """Verify the challenge MAC and move a reunite scan into CONNECTING."""

        request = self._request
        if (
            self._state is not DevicePairingState.DISCOVERING
            or not self._reuniting
            or not isinstance(request, LinkReuniteRequest)
        ):
            raise PairingSessionError("invalid_state_transition")
        if (
            not self._matches_response(
                request_id=response.request_id,
                daemon_instance_id=response.daemon_instance_id,
            )
            or response.nonce != request.nonce
            or response.target_mode != request.target_mode
            or not peer_ip
        ):
            raise PairingSessionError("pairing_credential_invalid")
        assert self._reunite_secret is not None
        expected_mac = reunite_response_mac(
            self._reunite_secret,
            request_id=request.request_id,
            nonce=request.nonce,
            daemon_instance_id=self.daemon_instance_id,
            target_mode=request.target_mode,
        )
        if not hmac.compare_digest(expected_mac, response.response_mac):
            raise PairingSessionError("pairing_credential_invalid")

        # The scanner stops broadcasting on accept; reconnects of this session
        # reuse its credentials without reseeding the binding secret.
        self._reuniting = False
        self._reunite_secret = None
        self._session_token = response.session_token
        self._expected_peer_ip = peer_ip
        self._state = DevicePairingState.CONNECTING
        self._deadline = now + self._connect_timeout_seconds

    def take_manual_binding_token(self) -> str | None:
        """Return and consume the manual pairing token eligible for binding.

        Yields the session token exactly once per manual pairing: after the
        hello that first established the current CONNECTED slot. Reconnects
        and reunite sessions never produce one.
        """

        token = self._pending_manual_token
        if self._state is not DevicePairingState.CONNECTED or token is None:
            return None
        self._pending_manual_token = None
        return token

    def device_disconnected(self, *, now: float) -> None:
        if self._state is not DevicePairingState.CONNECTED:
            raise PairingSessionError("invalid_state_transition")
        self._state = DevicePairingState.RECONNECTING
        self._pending_manual_token = None
        self._deadline = now + self._reconnect_timeout_seconds

    def cancel(self) -> bool:
        if self._state is DevicePairingState.IDLE:
            return False
        if self._state not in {
            DevicePairingState.DISCOVERING,
            DevicePairingState.CONNECTING,
        }:
            raise PairingSessionError("invalid_state_transition")
        self._reset(last_error="pairing_cancelled")
        return True

    def pending_cancel_message(self) -> PairCancel | None:
        """Build the authenticated UDP cancellation before clearing memory."""

        if (
            self._state is not DevicePairingState.CONNECTING
            or self._request is None
            or self._session_token is None
        ):
            return None
        return PairCancel(
            request_id=self._request.request_id,
            daemon_instance_id=self.daemon_instance_id,
            session_token=self._session_token,
        )

    def release(self) -> None:
        """Release a connected/reconnecting slot after explicit session end."""

        self._reset(last_error=None)

    def end_device_session(self, *, pair_request_id: str) -> None:
        """Release only the current connected session reported by hardware."""

        if self._state is not DevicePairingState.CONNECTED or self._request is None:
            raise PairingSessionError("pairing_session_required")
        if pair_request_id != self._request.request_id:
            raise PairingSessionError("pairing_credential_invalid")
        self._reset(last_error=None)

    def expire(self, *, now: float) -> bool:
        if self._deadline is None or now < self._deadline:
            return False

        if self._state is DevicePairingState.DISCOVERING and self._reuniting:
            error = "reunite_unavailable"
        elif self._state is DevicePairingState.DISCOVERING:
            error = "pairing_not_found"
        elif self._state is DevicePairingState.CONNECTING:
            error = "device_connect_timeout"
        elif self._state is DevicePairingState.RECONNECTING:
            error = "reconnect_timeout"
        else:
            return False
        self._reset(last_error=error)
        return True

    def _matches_response(
        self,
        *,
        request_id: str,
        daemon_instance_id: str,
    ) -> bool:
        return (
            self._request is not None
            and request_id == self._request.request_id
            and daemon_instance_id == self.daemon_instance_id
        )

    def _reset(self, *, last_error: str | None) -> None:
        self._state = DevicePairingState.IDLE
        self._request = None
        self._session_token = None
        self._expected_peer_ip = None
        self._deadline = None
        self._last_error = last_error
        self._reuniting = False
        self._reunite_secret = None
        self._pending_manual_token = None
