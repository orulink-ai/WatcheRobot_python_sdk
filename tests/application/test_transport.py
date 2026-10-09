from __future__ import annotations

import asyncio
import json
import struct
import threading

import pytest

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.protocol import FRAME_VIDEO
from watcherobot.runtime.daemon.application.session import ApplicationChannel


def test_resource_evidence_pairs_payload_receipt_and_device_generation(monkeypatch):
    from watcherobot.robot import WatcheRobot
    import watcherobot.application.transport as transport_module
    transport = DaemonApplicationTransport()
    clock = [100.0]
    monkeypatch.setattr(transport_module.time, "monotonic", lambda: clock[0])

    def receive(kind, data):
        asyncio.run(transport._on_frame(ApplicationChannel.DEVICE,
                    json.dumps({"type": kind, "code": 0, "data": data})))

    receive("evt.sdk.ready", {"device_id": "first", "capabilities": []})
    receive("evt.sdk.resource_snapshot", {"sequence": 1, "memory": {"free": 10}})
    robot = WatcheRobot._from_transport(transport)
    evidence = robot.resource_evidence
    assert evidence["device_id"] == "first" and evidence["received_at"] == 100.0
    assert evidence["consistent"] is True
    evidence["snapshot"]["memory"]["free"] = 999
    assert transport.resource_snapshot["memory"]["free"] == 10
    receive("evt.sdk.ready", {"device_id": "second", "capabilities": []})
    assert robot.resource_evidence["consistent"] is False
    clock[0] = 101.0
    receive("evt.sdk.resource_snapshot", {"sequence": 2})
    assert robot.resource_evidence["consistent"] is True
    assert robot.resource_evidence["received_at"] == 101.0


def test_resource_evidence_keeps_baseline_origin_across_reconnect():
    transport = DaemonApplicationTransport()

    async def send(kind, data):
        await transport._on_frame(ApplicationChannel.DEVICE, json.dumps({"type": kind, "data": data}))

    asyncio.run(send("evt.sdk.ready", {"device_id": "robot-a"}))
    asyncio.run(send("evt.sdk.resource_snapshot", {"stage": "baseline", "sequence": 1}))
    initial = transport.resource_evidence
    assert initial["baseline_generation"] == initial["generation"]
    assert initial["baseline_device_id"] == "robot-a"
    asyncio.run(send("evt.sdk.ready", {"device_id": "robot-a"}))
    asyncio.run(send("evt.sdk.resource_snapshot", {"stage": "rtc_running", "sequence": 2}))
    current = transport.resource_evidence
    assert current["consistent"] is True
    assert current["baseline_generation"] != current["generation"]
    asyncio.run(send("evt.sdk.resource_snapshot", {"stage": "baseline", "sequence": 3}))
    assert transport.resource_evidence["baseline_generation"] == current["generation"]


def test_resource_evidence_read_holds_event_updates_until_copy_finishes(monkeypatch):
    import watcherobot.application.transport as transport_module
    transport = DaemonApplicationTransport()
    old = {"sequence": 1}
    transport.resource_snapshot = old
    transport.resource_snapshot_received_at = 100.0
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()
    original = transport_module.deepcopy
    result = []

    def delayed_copy(value):
        if value is old:
            entered.set()
            assert release.wait(timeout=3.0)
        return original(value)

    monkeypatch.setattr(transport_module, "deepcopy", delayed_copy)
    reader = threading.Thread(target=lambda: result.append(transport.resource_evidence))
    reader.start()
    assert entered.wait(timeout=1.0)

    def update():
        asyncio.run(transport._on_frame(ApplicationChannel.DEVICE,
                    json.dumps({"type": "evt.sdk.resource_snapshot", "code": 0, "data": {"sequence": 2}})))
        updated.set()

    writer = threading.Thread(target=update)
    writer.start()
    try:
        assert not updated.wait(timeout=0.1)
    finally:
        release.set()
        reader.join(timeout=3.0)
        writer.join(timeout=3.0)
    assert updated.is_set() and not reader.is_alive()
    assert result[0]["snapshot"]["sequence"] == 1
    assert result[0]["received_at"] == 100.0
    assert transport.resource_evidence["snapshot"]["sequence"] == 2


def test_connected_application_requests_current_device_capabilities() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[tuple[ApplicationChannel, str | bytes]] = []
        command_sent = asyncio.Event()

        async def capture(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            sent.append((channel, frame))
            command_sent.set()

        transport._send = capture  # type: ignore[method-assign]

        connected = asyncio.create_task(transport._on_channels_connected())
        await asyncio.wait_for(command_sent.wait(), timeout=0.1)

        assert not connected.done()
        assert not transport._started_event.is_set()

        assert len(sent) == 1
        channel, frame = sent[0]
        assert channel is ApplicationChannel.DEVICE
        assert isinstance(frame, str)
        payload = json.loads(frame)
        assert payload["type"] == "sys.sdk.ready.get"
        assert payload["code"] == 0
        assert isinstance(payload["data"]["command_id"], str)
        assert payload["data"]["command_id"]
        command_id = payload["data"]["command_id"]
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.sdk.ready",
                    "code": 0,
                    "data": {
                        "capabilities": ["behavior", "motion"],
                        "firmware_version": "V3.1",
                    },
                }
            ),
        )
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "data": {"command_id": command_id},
                }
            ),
        )
        await asyncio.wait_for(connected, timeout=0.1)

        assert transport.capabilities == ("behavior", "motion")
        assert transport.device_info["firmware_version"] == "V3.1"
        assert transport._started_event.is_set()

    asyncio.run(scenario())


def test_audio_stream_waits_for_device_buffer_credit() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        task = asyncio.create_task(
            transport._send_audio_stream(
                b"\x00\x01" * 5,
                stream_id=7,
                chunk_bytes=2,
            )
        )

        for _ in range(100):
            if len(sent) == 4:
                break
            await asyncio.sleep(0)
        assert len(sent) == 4
        assert not task.done()

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 7,
                        "pending_frames": 3,
                        "queue_depth": 8,
                    },
                }
            ),
        )
        await asyncio.wait_for(task, timeout=1.0)
        assert len(sent) == 5

    asyncio.run(scenario())


def test_audio_stream_prefills_the_device_start_buffer_before_waiting() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        task = asyncio.create_task(
            transport._send_audio_stream(b"\x00" * (4096 * 17), stream_id=9, chunk_bytes=4096)
        )

        for _ in range(100):
            if len(sent) == 4:
                break
            await asyncio.sleep(0)
        assert len(sent) == 4

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 9,
                        "playing": False,
                        "pending_frames": 4,
                        "queue_depth": 16,
                        "start_buffer_frames": 16,
                    },
                }
            ),
        )
        for _ in range(100):
            if len(sent) == 12:
                break
            await asyncio.sleep(0)
        assert len(sent) == 12

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 9,
                        "playing": False,
                        "pending_frames": 12,
                        "queue_depth": 16,
                        "start_buffer_frames": 16,
                    },
                }
            ),
        )
        for _ in range(100):
            if len(sent) == 16:
                break
            await asyncio.sleep(0)
        assert len(sent) == 16

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 9,
                        "playing": True,
                        "pending_frames": 7,
                        "queue_depth": 16,
                        "start_buffer_frames": 16,
                    },
                }
            ),
        )
        await asyncio.wait_for(task, timeout=1.0)
        assert len(sent) == 17

    asyncio.run(scenario())


def test_public_audio_stream_rejects_non_device_slot_chunk_size() -> None:
    transport = DaemonApplicationTransport()

    with pytest.raises(ValueError, match="chunk_bytes must be 4096"):
        transport.send_audio_stream(b"\x00" * 4096, stream_id=1, chunk_bytes=2048)


def test_transport_dispatches_face_preview_packet_as_video_frame() -> None:
    transport = DaemonApplicationTransport()
    received = []
    transport.set_callbacks(lambda _message: None, received.append, lambda: None)
    jpeg = b"\xff\xd8preview\xff\xd9"
    packet = struct.pack(
        "<4sBBHIIHHI",
        b"FTW1",
        1,
        1,
        24,
        42,
        1000,
        416,
        416,
        len(jpeg),
    ) + jpeg

    transport._dispatch_binary(packet)

    assert len(received) == 1
    assert received[0].frame_type == FRAME_VIDEO
    assert received[0].sequence == 42
    assert received[0].payload == packet


def test_transport_dispatches_face_preview_telemetry_as_sdk_event() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport()
        received = []
        transport.set_callbacks(received.append, lambda _frame: None, lambda: None)
        telemetry = {
            "v": 1,
            "kind": "frame",
            "seq": 42,
            "size": [416, 416],
        }

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(telemetry),
        )

        assert received == [
            {
                "type": "evt.face_tracking.preview.frame",
                "code": 0,
                "data": telemetry,
            }
        ]

    asyncio.run(scenario())


def test_transport_exposes_raw_device_send_and_fans_out_protocol_messages() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport()
        sent: list[tuple[ApplicationChannel, str | bytes]] = []
        primary: list[dict[str, object]] = []
        observed: list[dict[str, object]] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            sent.append((channel, frame))

        transport._send = capture  # type: ignore[method-assign]
        transport.set_callbacks(primary.append, lambda _frame: None, lambda: None)
        transport.add_message_listener(observed.append)
        message = {
            "type": "evt.rtc.state",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "data": {"state": "connected"},
        }

        await transport._send_device(json.dumps(message))
        await transport._on_frame(ApplicationChannel.DEVICE, json.dumps(message))

        assert sent == [(ApplicationChannel.DEVICE, json.dumps(message))]
        assert primary == [message]
        assert observed == [message]

    asyncio.run(scenario())


def test_ready_event_sanitizes_and_deduplicates_the_animation_catalog() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.sdk.ready",
                    "code": 0,
                    "data": {
                        "animations": [
                            "boot",
                            "standby_little4",
                            "boot",
                            "../unsafe",
                            "UPPER",
                            7,
                        ]
                    },
                }
            ),
        )

        assert transport.animation_ids == ("boot", "standby_little4")

    asyncio.run(scenario())


def test_transport_keeps_resource_baseline_latest_snapshot_and_history() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport()
        baseline = {
            "type": "evt.sdk.resource_snapshot",
            "code": 0,
            "data": {
                "sequence": 2,
                "stage": "baseline",
                "captured_at_ms": 100,
                "memory": {"internal": {"free_bytes": 120000}},
            },
        }
        release = {
            "type": "evt.sdk.resource_snapshot",
            "code": 0,
            "data": {
                "sequence": 3,
                "stage": "rtc_release_1000ms",
                "captured_at_ms": 4200,
                "memory": {
                    "internal": {
                        "free_bytes": 48000,
                        "largest_free_block_bytes": 24000,
                        "minimum_free_bytes": 12000,
                    }
                },
                "resources": {"rtc": False, "media_system": False},
                "release": {"complete": True, "failures": []},
            },
        }
        rtc_baseline = {
            "type": "evt.sdk.resource_snapshot",
            "code": 0,
            "data": {
                "sequence": 3,
                "stage": "rtc_pre_start",
                "captured_at_ms": 1200,
                "memory": {"internal": {"free_bytes": 52000}},
            },
        }
        release["data"]["sequence"] = 4

        await transport._on_frame(ApplicationChannel.DEVICE, json.dumps(baseline))
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(rtc_baseline),
        )
        await transport._on_frame(ApplicationChannel.DEVICE, json.dumps(release))

        assert transport.resource_baseline == baseline["data"]
        assert transport.resource_rtc_baseline == rtc_baseline["data"]
        assert transport.resource_snapshot == release["data"]
        assert transport.resource_history == [
            baseline["data"],
            rtc_baseline["data"],
            release["data"],
        ]

        reconnected_baseline = {
            **baseline,
            "data": {**baseline["data"], "sequence": 4, "captured_at_ms": 9000},
        }
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(reconnected_baseline),
        )
        assert transport.resource_baseline == reconnected_baseline["data"]
        assert transport.resource_rtc_baseline == {}
        assert transport.resource_history == [reconnected_baseline["data"]]

    asyncio.run(scenario())


def test_resource_snapshot_tracks_receipt_time_even_when_payload_repeats(monkeypatch) -> None:
    import watcherobot.application.transport as module
    current = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: current[0])

    async def scenario() -> None:
        transport = DaemonApplicationTransport()
        assert transport.resource_snapshot_received_at is None
        frame = json.dumps({"type": "evt.sdk.resource_snapshot", "code": 0,
                            "data": {"sequence": 1, "captured_at_ms": 40}})
        await transport._on_frame(ApplicationChannel.DEVICE, frame)
        assert transport.resource_snapshot_received_at == 100.0
        current[0] += 6.0
        await transport._on_frame(ApplicationChannel.DEVICE, frame)
        assert transport.resource_snapshot_received_at == 106.0

    asyncio.run(scenario())
