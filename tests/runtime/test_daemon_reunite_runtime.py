"""End-to-end reunite flows over real sockets and a temp bindings store."""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
from pathlib import Path

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.pairing.bindings_store import DeviceBindingsStore
from watcherobot.runtime.daemon.pairing.protocol import (
    LinkReuniteAccept,
    derive_binding_secret,
    encode_udp_message,
    reunite_response_mac,
)
from watcherobot.runtime.daemon.runtime import DaemonRuntime
from tests.runtime.pairing_helpers import (
    SESSION_TOKEN,
    connect_runtime_hardware,
)

REUNITE_TOKEN = "d" * 64


def _runtime(tmp_path: Path, **options) -> DaemonRuntime:
    return DaemonRuntime(
        application_dir=tmp_path / "unselected",
        current_app="unselected",
        managed_app_root=tmp_path / "managed",
        bundled_resource_root=tmp_path / "bundled",
        external_host="127.0.0.1",
        external_port=0,
        control_port=0,
        pairing_udp_port=0,
        preview_udp_port=0,
        device_bindings_store=DeviceBindingsStore(tmp_path),
        **options,
    )


def _bindings_file(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "device-bindings.json").read_text(encoding="utf-8")
    )


def _stored_device(tmp_path: Path):
    return _bindings_file(tmp_path)["device"]


def _hello_frame(
    runtime: DaemonRuntime,
    *,
    pairing_version: str,
    pair_request_id: str,
    session_token: str,
    mode: str = "python_sdk",
) -> str:
    return json.dumps(
        {
            "type": "sys.client.hello",
            "code": 0,
            "data": {
                "role": "hardware",
                "pairing_protocol": "watcher-lan-pairing",
                "pairing_version": pairing_version,
                "pair_request_id": pair_request_id,
                "daemon_instance_id": runtime.device_pairing.daemon_instance_id,
                "session_token": session_token,
                "mode": mode,
            },
        }
    )


async def _open_ws(runtime: DaemonRuntime):
    options = (
        {"proxy": None}
        if "proxy" in inspect.signature(connect).parameters
        else {}
    )
    return await connect(runtime.external_server.url, max_size=None, **options)


async def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    for _ in range(int(timeout_s * 100)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _scenario_manual_pairing_seeds_durable_binding(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()
    try:
        identity = _bindings_file(tmp_path)
        assert identity["daemon_instance_id"] == (
            runtime.device_pairing.daemon_instance_id
        )
        assert identity["device"] is None

        websocket = await connect_runtime_hardware(runtime)
        try:
            stored = _stored_device(tmp_path)
            assert stored["binding_secret"] == derive_binding_secret(SESSION_TOKEN)
            assert stored["target_mode"] == "python_sdk"
            assert stored["last_peer_ip"] == "127.0.0.1"
            assert stored["last_ws_port"] == runtime.external_server.bound_port
        finally:
            await websocket.close()
        assert await _wait_until(
            lambda: runtime.device_pairing.state.value != "connected"
        )
    finally:
        await runtime.stop()


async def _scenario_restart_autoreunites_without_pairing_code(tmp_path: Path) -> None:
    # First lifecycle: manual pairing arms the durable binding.
    first = _runtime(tmp_path)
    await first.start()
    websocket = await connect_runtime_hardware(first)
    await websocket.close()
    await _wait_until(lambda: first.device_pairing.state.value != "connected")
    seeded_secret = _stored_device(tmp_path)["binding_secret"]
    await first.stop()

    # Second lifecycle: boot must open an automatic reunite scan.
    second = _runtime(tmp_path)
    assert second.device_pairing.daemon_instance_id == (
        first.device_pairing.daemon_instance_id
    )
    await second.start()
    try:
        assert second.device_pairing.state.value == "discovering"
        assert second.device_pairing.reuniting is True

        request = second.device_pairing.current_request
        accept = LinkReuniteAccept(
            request_id=request.request_id,
            daemon_instance_id=second.device_pairing.daemon_instance_id,
            nonce=request.nonce,
            target_mode="python_sdk",
            response_mac=reunite_response_mac(
                seeded_secret,
                request_id=request.request_id,
                nonce=request.nonce,
                daemon_instance_id=second.device_pairing.daemon_instance_id,
                target_mode="python_sdk",
            ),
            session_token=REUNITE_TOKEN,
        )
        assert await second.pairing_udp.handle_datagram(
            encode_udp_message(accept),
            ("127.0.0.1", 40000),
        )
        assert second.device_pairing.state.value == "connecting"

        ws = await _open_ws(second)
        try:
            await ws.send(
                _hello_frame(
                    second,
                    pairing_version="1.1",
                    pair_request_id=request.request_id,
                    session_token=REUNITE_TOKEN,
                )
            )
            acknowledgement = json.loads(await ws.recv())
            assert acknowledgement["data"]["session_state"] == "connected"
            assert second.device_pairing.state.value == "connected"

            # Reunite sessions never reseed the long-term binding secret.
            assert _stored_device(tmp_path)["binding_secret"] == seeded_secret
        finally:
            await ws.close()
    finally:
        await second.stop()


async def _scenario_desktop_disconnect_clears_the_binding(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()
    websocket = await connect_runtime_hardware(runtime)
    await websocket.close()
    await _wait_until(lambda: runtime.device_pairing.state.value != "connected")

    disconnected = await runtime.disconnect_device()

    assert disconnected is True
    assert _stored_device(tmp_path) is None
    await runtime.stop()


def test_manual_pairing_seeds_durable_binding(tmp_path: Path) -> None:
    asyncio.run(_scenario_manual_pairing_seeds_durable_binding(tmp_path))


def test_restart_autoreunites_without_pairing_code(tmp_path: Path) -> None:
    asyncio.run(_scenario_restart_autoreunites_without_pairing_code(tmp_path))


def test_desktop_disconnect_clears_the_binding(tmp_path: Path) -> None:
    asyncio.run(_scenario_desktop_disconnect_clears_the_binding(tmp_path))


async def _seed_binding(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()
    try:
        websocket = await connect_runtime_hardware(runtime)
        request = runtime.device_pairing.current_request
        await websocket.send(json.dumps({
            "type": "sys.device.session.end", "code": 0,
            "data": {"pair_request_id": request.request_id, "reason": "mode_exit"},
        }))
        await websocket.recv()
        await websocket.wait_closed()
    finally:
        await runtime.stop()


def test_expired_boot_scan_retries_without_restarting_daemon(tmp_path: Path) -> None:
    async def scenario():
        await _seed_binding(tmp_path)
        now = [0.0]
        runtime = _runtime(tmp_path, clock=lambda: now[0])
        await runtime.start()
        try:
            first_id = runtime.device_pairing.current_request.request_id
            now[0] = 11.0
            assert await runtime.pairing_udp.expire_once()
            await asyncio.sleep(0.7)
            assert runtime.device_pairing.state.value == "idle"
            now[0] = 31.0
            assert await _wait_until(lambda: runtime.device_pairing.reuniting)
            assert runtime.device_pairing.current_request.request_id != first_id
            request = runtime.device_pairing.current_request
            secret = _stored_device(tmp_path)["binding_secret"]
            accept = LinkReuniteAccept(
                request_id=request.request_id,
                daemon_instance_id=runtime.device_pairing.daemon_instance_id,
                nonce=request.nonce,
                target_mode="python_sdk",
                response_mac=reunite_response_mac(
                    secret, request_id=request.request_id, nonce=request.nonce,
                    daemon_instance_id=runtime.device_pairing.daemon_instance_id,
                    target_mode="python_sdk",
                ),
                session_token=REUNITE_TOKEN,
            )
            assert await runtime.pairing_udp.handle_datagram(
                encode_udp_message(accept), ("127.0.0.1", 40000)
            )
            ws = await _open_ws(runtime)
            try:
                await ws.send(_hello_frame(
                    runtime, pairing_version="1.1",
                    pair_request_id=request.request_id, session_token=REUNITE_TOKEN,
                ))
                assert json.loads(await ws.recv())["data"]["session_state"] == "connected"
                now[0] = 1000.0
                await asyncio.sleep(1.1)
                assert runtime.device_pairing.state.value == "connected"
                assert runtime.device_pairing.current_request is request
            finally:
                await ws.close()
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_mode_exit_restarts_credential_discovery(tmp_path: Path) -> None:
    async def scenario():
        runtime = _runtime(tmp_path)
        await runtime.start()
        try:
            device = await connect_runtime_hardware(runtime)
            request = runtime.device_pairing.current_request
            await device.send(json.dumps({
                "type": "sys.device.session.end", "code": 0,
                "data": {"pair_request_id": request.request_id, "reason": "mode_exit"},
            }))
            ack = json.loads(await device.recv())
            assert ack["data"]["type"] == "sys.device.session.end"
            await device.wait_closed()
            assert await _wait_until(lambda: runtime.device_pairing.reuniting)
            assert _stored_device(tmp_path) is not None
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_disconnect_forgets_during_scan_and_while_idle(tmp_path: Path) -> None:
    async def scenario():
        for expire_first in (False, True):
            await _seed_binding(tmp_path)
            now = [0.0]
            runtime = _runtime(tmp_path, clock=lambda: now[0])
            await runtime.start()
            try:
                if expire_first:
                    now[0] = 11.0
                    assert await runtime.pairing_udp.expire_once()
                assert await runtime.disconnect_device()
                assert _stored_device(tmp_path) is None
                now[0] = 100.0
                await asyncio.sleep(0.7)
                assert runtime.device_pairing.state.value == "idle"
            finally:
                await runtime.stop()
    asyncio.run(scenario())


def test_manual_pairing_supersedes_automatic_discovery(tmp_path: Path) -> None:
    async def scenario():
        await _seed_binding(tmp_path)
        runtime = _runtime(tmp_path)
        await runtime.start()
        try:
            assert runtime.device_pairing.reuniting
            await runtime.pair_device("123456", "desktop_link")
            request = runtime.device_pairing.current_request
            assert request.pairing_code == "123456"
            assert request.target_mode == "desktop_link"
            assert runtime.device_pairing.reuniting is False
            await asyncio.sleep(0.7)
            assert runtime.device_pairing.current_request is request
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_availability_wakes_idle_reconnect(tmp_path: Path) -> None:
    async def scenario():
        await _seed_binding(tmp_path)
        now = [0.0]
        runtime = _runtime(tmp_path, clock=lambda: now[0])
        await runtime.start()
        try:
            now[0] = 11.0
            assert await runtime.pairing_udp.expire_once()
            def available(**overrides):
                return json.dumps({
                    "type": "link.reunite.available",
                    "protocol": "watcher-lan-pairing", "version": "1.1",
                    "daemon_instance_id": runtime.device_pairing.daemon_instance_id,
                    "target_mode": "python_sdk", **overrides,
                }).encode()
            async def receive(**overrides):
                return await runtime.pairing_udp.handle_datagram(available(**overrides), ("127.0.0.1", 37021))
            assert not await receive(daemon_instance_id="0" * 32)
            assert runtime.device_pairing.state.value == "idle"
            assert not await receive(extra="invalid")
            assert not await runtime.pairing_udp.handle_datagram(available(), ("192.0.2.99", 37021))
            interface = runtime.pairing_udp.active_interfaces[0]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.bind(("127.0.0.1", 0))
                sender.sendto(available(), (interface.local_ip, runtime.pairing_udp.bound_port))
            assert await _wait_until(lambda: runtime.device_pairing.reuniting)
            request = runtime.device_pairing.current_request
            assert request is not None and runtime.device_pairing.reuniting
            assert not await receive()
            assert runtime.device_pairing.current_request is request
            await runtime.pair_device("123456", "desktop_link")
            manual = runtime.device_pairing.current_request
            assert not await receive()
            assert runtime.device_pairing.current_request is manual
            await runtime.cancel_device_pairing()
            assert not await receive()
            assert runtime.device_pairing.state.value == "idle"
            await runtime.disconnect_device()
            assert not await receive()
            assert runtime.device_pairing.state.value == "idle"
        finally:
            await runtime.stop()
    asyncio.run(scenario())
