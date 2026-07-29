"""High-level orchestration for Bluetooth Wi-Fi provisioning."""

from __future__ import annotations

import asyncio
import time
import uuid

from .backend import BleConnection, BluetoothBackend
from .errors import (
    ProvisioningCancelledError,
    ProvisioningProtocolError,
    ProvisioningRejectedError,
    ProvisioningResponseTimeoutError,
)
from .models import (
    BluetoothDevice,
    ProtocolMessage,
    ProvisioningResult,
    WifiStatus,
)
from .protocol import (
    BLE_CHARACTERISTIC_UUID,
    JsonMessageBuffer,
    build_request,
)

DEFAULT_SCAN_TIMEOUT = 10.0
DEFAULT_CONNECT_TIMEOUT = 12.0
DEFAULT_RESPONSE_TIMEOUT = 3.0


class BluetoothProvisioner:
    """Asynchronous, cross-platform BLE provisioning client."""

    def __init__(
        self,
        *,
        backend: BluetoothBackend | None = None,
        scan_timeout: float = DEFAULT_SCAN_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> None:
        if backend is None:
            from .bleak_backend import BleakBackend

            backend = BleakBackend()
        self._backend = backend
        self._scan_timeout = scan_timeout
        self._connect_timeout = connect_timeout
        self._response_timeout = response_timeout

    async def scan_devices(
        self,
        *,
        timeout: float | None = None,
        name_filter: str | None = None,
    ) -> list[BluetoothDevice]:
        try:
            return await self._backend.scan_devices(
                timeout=self._scan_timeout if timeout is None else timeout,
                name_filter=name_filter,
            )
        except asyncio.CancelledError as exc:
            raise ProvisioningCancelledError() from exc

    async def provision_wifi(
        self,
        device: BluetoothDevice,
        *,
        ssid: str,
        password: str,
        clear_existing: bool = False,
    ) -> ProvisioningResult:
        _validate_credentials(ssid, password)
        try:
            session = await self._open_session(device)
            try:
                if clear_existing:
                    await session.request_status("cfg.wifi.clear")
                ack = await session.request_ack(
                    "cfg.wifi.set",
                    {"ssid": ssid, "password": password},
                )
                return ProvisioningResult(
                    device=device,
                    ssid=ssid,
                    state="credentials_saved",
                    ack=ack,
                )
            finally:
                await _finish_cleanup(session)
        except asyncio.CancelledError as exc:
            raise ProvisioningCancelledError() from exc

    async def get_wifi_status(
        self,
        device: BluetoothDevice,
    ) -> WifiStatus:
        try:
            session = await self._open_session(device)
            try:
                return await session.request_status("cfg.wifi.get")
            finally:
                await _finish_cleanup(session)
        except asyncio.CancelledError as exc:
            raise ProvisioningCancelledError() from exc

    async def clear_wifi(self, device: BluetoothDevice) -> WifiStatus:
        try:
            session = await self._open_session(device)
            try:
                return await session.request_status("cfg.wifi.clear")
            finally:
                await _finish_cleanup(session)
        except asyncio.CancelledError as exc:
            raise ProvisioningCancelledError() from exc

    async def _open_session(
        self,
        device: BluetoothDevice,
    ) -> _ProvisioningSession:
        connection = await self._backend.connect(
            device,
            timeout=self._connect_timeout,
        )
        session = _ProvisioningSession(
            connection,
            response_timeout=self._response_timeout,
        )
        try:
            await session.start()
        except BaseException:
            await _finish_cleanup(session)
            raise
        return session


class _ProvisioningSession:
    def __init__(
        self,
        connection: BleConnection,
        *,
        response_timeout: float,
    ) -> None:
        self._connection = connection
        self._response_timeout = response_timeout
        self._messages: asyncio.Queue[
            tuple[float, ProtocolMessage | ProvisioningProtocolError]
        ] = asyncio.Queue()
        self._decoder = JsonMessageBuffer()
        self._notifications_started = False
        self._closed = False

    async def start(self) -> None:
        if (
            self._connection.characteristic_uuid.lower()
            != BLE_CHARACTERISTIC_UUID
        ):
            raise ProvisioningProtocolError(
                "Connected backend returned the wrong characteristic"
            )
        await self._connection.start_notifications(
            self._handle_notification
        )
        self._notifications_started = True

    async def request_ack(
        self,
        command_type: str,
        data: dict[str, object] | None = None,
    ) -> ProtocolMessage:
        command_id = _new_command_id(command_type)
        payload_data = dict(data or {})
        payload_data["command_id"] = command_id
        payload = build_request(command_type, payload_data)
        del payload_data

        self._discard_queued_messages()
        request_started = time.monotonic()
        deadline = time.monotonic() + self._response_timeout
        try:
            await asyncio.wait_for(
                self._connection.write(payload),
                timeout=self._response_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ProvisioningResponseTimeoutError(
                command_type,
                self._response_timeout,
            ) from exc
        finally:
            del payload

        await self._read_cached_response(deadline)
        return await self._wait_for(
            command_type,
            command_id,
            deadline,
            request_started,
            accept_status=False,
        )

    async def request_status(self, command_type: str) -> WifiStatus:
        command_id = _new_command_id(command_type)
        payload = build_request(
            command_type,
            {"command_id": command_id},
        )
        self._discard_queued_messages()
        request_started = time.monotonic()
        deadline = time.monotonic() + self._response_timeout
        try:
            await asyncio.wait_for(
                self._connection.write(payload),
                timeout=self._response_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ProvisioningResponseTimeoutError(
                command_type,
                self._response_timeout,
            ) from exc
        finally:
            del payload

        await self._read_cached_response(deadline)
        message = await self._wait_for(
            command_type,
            command_id,
            deadline,
            request_started,
            accept_status=True,
        )
        if message.status is None:
            message = await self._wait_for_status(
                command_type,
                command_id,
                deadline,
                request_started,
            )
        if message.status is None:
            raise ProvisioningProtocolError(
                "Wi-Fi status response has no state"
            )
        return WifiStatus(
            state=message.status,
            ssid=message.ssid,
            ip=message.ip,
        )

    async def _read_cached_response(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            cached = await asyncio.wait_for(
                self._connection.read(),
                timeout=remaining,
            )
        except Exception:
            return
        if cached:
            self._handle_notification(cached)

    async def _wait_for(
        self,
        command_type: str,
        command_id: str,
        deadline: float,
        request_started: float,
        *,
        accept_status: bool,
    ) -> ProtocolMessage:
        while True:
            message = await self._next_message(
                command_type,
                deadline,
                request_started,
            )
            if isinstance(message, ProvisioningProtocolError):
                raise message
            if message.type == "evt.wifi.status" and accept_status:
                return self._prefer_matching_rejection(
                    message,
                    command_type,
                    command_id,
                    request_started,
                )
            if (
                message.command_type != command_type
                or message.command_id != command_id
            ):
                continue
            if message.type == "sys.nack":
                raise ProvisioningRejectedError(
                    command_type,
                    reason=message.reason or "rejected",
                    code=message.code or -1,
                )
            if message.type == "sys.ack":
                return message

    def _prefer_matching_rejection(
        self,
        status: ProtocolMessage,
        command_type: str,
        command_id: str,
        request_started: float,
    ) -> ProtocolMessage:
        candidate = status
        while True:
            try:
                received_at, message = self._messages.get_nowait()
            except asyncio.QueueEmpty:
                return candidate
            if received_at < request_started:
                continue
            if isinstance(message, ProvisioningProtocolError):
                raise message
            if message.type == "evt.wifi.status":
                candidate = message
                continue
            if (
                message.type == "sys.nack"
                and message.command_type == command_type
                and message.command_id == command_id
            ):
                raise ProvisioningRejectedError(
                    command_type,
                    reason=message.reason or "rejected",
                    code=message.code or -1,
                )
    async def _wait_for_status(
        self,
        command_type: str,
        command_id: str,
        deadline: float,
        request_started: float,
    ) -> ProtocolMessage:
        while True:
            message = await self._next_message(
                command_type,
                deadline,
                request_started,
            )
            if isinstance(message, ProvisioningProtocolError):
                raise message
            if message.type == "evt.wifi.status":
                return message
            if (
                message.type == "sys.nack"
                and message.command_type == command_type
                and message.command_id == command_id
            ):
                raise ProvisioningRejectedError(
                    command_type,
                    reason=message.reason or "rejected",
                    code=message.code or -1,
                )

    async def _next_message(
        self,
        command_type: str,
        deadline: float,
        request_started: float,
    ) -> ProtocolMessage | ProvisioningProtocolError:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProvisioningResponseTimeoutError(
                    command_type,
                    self._response_timeout,
                )
            try:
                received_at, message = await asyncio.wait_for(
                    self._messages.get(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError as exc:
                raise ProvisioningResponseTimeoutError(
                    command_type,
                    self._response_timeout,
                ) from exc
            if received_at >= request_started:
                return message

    def _handle_notification(self, fragment: bytes) -> None:
        try:
            messages = self._decoder.feed(fragment)
        except ProvisioningProtocolError as exc:
            self._messages.put_nowait((time.monotonic(), exc))
            return
        for message in messages:
            self._messages.put_nowait((time.monotonic(), message))

    def _discard_queued_messages(self) -> None:
        while True:
            try:
                self._messages.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._notifications_started:
            try:
                await self._connection.stop_notifications()
            except Exception:
                pass
        try:
            await self._connection.disconnect()
        except Exception:
            pass


def _new_command_id(command_type: str) -> str:
    command_name = command_type.rsplit(".", 1)[-1]
    return f"python-wifi-{command_name}-{uuid.uuid4().hex[:12]}"


def _validate_credentials(ssid: str, password: str) -> None:
    ssid_length = len(ssid.encode("utf-8"))
    if not ssid or ssid_length > 32:
        raise ValueError("SSID must be non-empty and at most 32 UTF-8 bytes")
    password_length = len(password.encode("utf-8"))
    if password_length > 64:
        raise ValueError("Wi-Fi password must be at most 64 UTF-8 bytes")


async def _finish_cleanup(session: _ProvisioningSession) -> None:
    cleanup_task = asyncio.create_task(session.close())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise
