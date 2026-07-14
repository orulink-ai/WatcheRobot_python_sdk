import hashlib
import threading
from concurrent.futures import Future

import pytest

from watcherobot import Job, WatcheRobot
from watcherobot.errors import CommandError
from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, FRAME_AUDIO, FRAME_IMAGE, BinaryFrame


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.message_callback = None
        self.binary_callback = None
        self.disconnect_callback = None
        self.capabilities = (
            "behavior",
            "animation",
            "motion",
            "audio",
            "audio.stream",
            "light",
            "microphone",
            "camera.capture",
        )
        self.device_info = {"device_id": "watcher-test", "firmware_version": "test"}
        self.next_operation_id = 1
        self.next_session_id = 100
        self.closed = False
        self.audio_streams = []

    def set_callbacks(self, message_callback, binary_callback, disconnect_callback):
        self.message_callback = message_callback
        self.binary_callback = binary_callback
        self.disconnect_callback = disconnect_callback

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, data))
        response = {"type": "sys.ack", "code": 0, "data": {}}
        if message_type.endswith(".play") or message_type == "ctrl.motion.move_to":
            response["data"]["operation_id"] = self.next_operation_id
            self.next_operation_id += 1
        if message_type in ("ctrl.microphone.open", "ctrl.camera.capture"):
            response["data"]["session_id"] = self.next_session_id
            self.next_session_id += 1
        return response

    def close(self):
        self.closed = True

    def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
        self.audio_streams.append((bytes(pcm), stream_id, chunk_bytes))
        future = Future()
        future.set_result(None)
        return future


def test_public_namespaces_build_protocol_commands():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    behavior = robot.behavior.play("greeting", repeat=2)
    motion = robot.motion.move_to(pan_deg=110, tilt_deg=120, duration_ms=500)
    robot.motion.set_target(pan_deg=105)
    animation = robot.animation.play("smile")
    audio = robot.audio.play("confirm")
    robot.lights.set_color("#4DA3FF", brightness=0.7)
    light_effect = robot.lights.play_effect(
        "breathing",
        color="#4DA3FF",
        brightness=0.7,
        period_ms=750,
        repeat=3,
    )

    assert all(isinstance(job, Job) for job in (behavior, motion, animation, audio, light_effect))
    assert transport.commands == [
        ("ctrl.behavior.play", {"behavior_id": "greeting", "repeat": 2}),
        (
            "ctrl.motion.move_to",
            {"pan_deg": 110, "tilt_deg": 120, "duration_ms": 500, "profile": "ease_in_out"},
        ),
        ("ctrl.motion.set_target", {"pan_deg": 105}),
        ("ctrl.animation.play", {"animation_id": "smile"}),
        ("ctrl.audio.play", {"sound_id": "confirm"}),
        ("ctrl.light.set", {"color": "#4DA3FF", "brightness": 0.7, "zone": "all"}),
        (
            "ctrl.light.effect.play",
            {
                "effect": "breathing",
                "color": "#4DA3FF",
                "brightness": 0.7,
                "zone": "all",
                "period_ms": 750,
                "repeat": 3,
            },
        ),
    ]


@pytest.mark.parametrize("duration_ms", [0, -1, 1.5, True, 65536])
def test_motion_move_to_rejects_invalid_duration_ms(duration_ms):
    robot = WatcheRobot._from_transport(FakeTransport())

    with pytest.raises(ValueError, match="duration_ms must be an integer between 1 and 65535"):
        robot.motion.move_to(pan_deg=110, tilt_deg=120, duration_ms=duration_ms)


@pytest.mark.parametrize("period_ms", [-1, 1.5, True, 65536])
def test_light_effect_rejects_invalid_period_ms(period_ms):
    robot = WatcheRobot._from_transport(FakeTransport())

    with pytest.raises(ValueError, match="period_ms must be an integer between 0 and 65535"):
        robot.lights.play_effect("breathing", period_ms=period_ms)


def test_light_effect_no_longer_accepts_ambiguous_period_seconds():
    robot = WatcheRobot._from_transport(FakeTransport())

    with pytest.raises(TypeError):
        robot.lights.play_effect("breathing", period=0.5)


def test_operation_event_updates_matching_job():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    job = robot.behavior.play("greeting")

    transport.message_callback(
        {"type": "evt.sdk.operation", "code": 0, "data": {"operation_id": job.id, "state": "completed"}}
    )

    assert job.wait(timeout=0) is job
    assert job.id not in robot._jobs


def test_audio_play_pcm_streams_binary_and_completes_from_device_status():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    pcm = b"\x01\x00\x02\x00"

    playback = robot.audio.play_pcm(pcm)

    assert isinstance(playback, Job)
    assert transport.commands == [
        (
            "ctrl.audio.stream.begin",
            {
                "stream_id": playback.id,
                "total_bytes": len(pcm),
                "sample_rate_hz": 24000,
                "channels": 1,
                "sample_width_bytes": 2,
                "audio_sha256": hashlib.sha256(pcm).hexdigest(),
            },
        )
    ]
    assert transport.audio_streams == [(pcm, playback.id, 4096)]

    transport.message_callback(
        {
            "type": "evt.audio.buffer_status",
            "code": 0,
            "data": {"reason": "playback", "stream_id": playback.id, "playing": True},
        }
    )
    assert playback.state.value == "running"

    transport.message_callback(
        {
            "type": "evt.audio.buffer_status",
            "code": 0,
            "data": {
                "reason": "complete",
                "stream_id": playback.id,
                "audio_sha256": hashlib.sha256(pcm).hexdigest(),
            },
        }
    )

    assert playback.wait(timeout=0).state.value == "completed"


def test_audio_playback_write_failure_is_a_terminal_failure():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    transport.message_callback(
        {
            "type": "evt.audio.buffer_status",
            "code": 0,
            "data": {
                "reason": "playback_write_failed",
                "stream_id": playback.id,
            },
        }
    )

    assert playback.state.value == "failed"


def test_new_audio_stream_cancels_previous_host_sender_before_replacement():
    class PendingAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.audio_streams.append((bytes(pcm), stream_id, chunk_bytes))
            future = Future()
            self.audio_streams[-1] += (future,)
            return future

    transport = PendingAudioTransport()
    robot = WatcheRobot._from_transport(transport)

    first = robot.audio.play_pcm(b"\x01\x00")
    first_future = transport.audio_streams[0][3]
    second = robot.audio.play_pcm(b"\x02\x00")

    assert first_future.cancelled()
    assert first.state.value == "cancelled"
    assert second.state.value == "starting"
    assert [command[0] for command in transport.commands] == [
        "ctrl.audio.stream.begin",
        "ctrl.audio.stream.begin",
    ]


def test_audio_playback_cancel_stops_sender_and_device():
    class PendingAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

    transport = PendingAudioTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    playback.cancel()

    assert transport.future.cancelled()
    assert playback.state.value == "cancelled"
    assert transport.commands[-1] == ("ctrl.audio.stop", {})


def test_operation_event_arriving_immediately_after_ack_is_not_lost():
    class EarlyEventTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            response = super().send_command(message_type, data, timeout)
            operation_id = response.get("data", {}).get("operation_id")
            if operation_id is not None:
                self.message_callback(
                    {
                        "type": "evt.sdk.operation",
                        "code": 0,
                        "data": {"operation_id": operation_id, "state": "completed"},
                    }
                )
            return response

    robot = WatcheRobot._from_transport(EarlyEventTransport())

    job = robot.behavior.play("greeting")

    assert job.wait(timeout=0).state.value == "completed"
    assert job.id not in robot._jobs


def test_disconnect_releases_tracked_jobs_after_failing_them():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    job = robot.behavior.play("greeting")

    transport.disconnect_callback()

    assert job.state.value == "failed"
    assert robot._jobs == {}
    assert robot._closed
    assert transport.closed


def test_motion_set_target_requires_at_least_one_axis():
    robot = WatcheRobot._from_transport(FakeTransport())

    try:
        robot.motion.set_target()
    except ValueError as error:
        assert "pan_deg" in str(error)
    else:
        raise AssertionError("set_target should reject an empty target")


def test_media_frame_arriving_before_open_ack_is_buffered():
    class EarlyMediaTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.microphone.open":
                self.binary_callback(BinaryFrame(FRAME_AUDIO, FLAG_FIRST, 0, 5, b"pcm"))
            if message_type == "ctrl.camera.capture":
                self.binary_callback(BinaryFrame(FRAME_IMAGE, FLAG_FIRST | FLAG_LAST, 0, 6, b"jpeg"))
            return super().send_command(message_type, data, timeout)

    robot = WatcheRobot._from_transport(EarlyMediaTransport())

    microphone = robot.microphone.open(queue_size=1)
    assert microphone.read(timeout=0).data == b"pcm"
    assert robot.camera.capture(timeout=0).data == b"jpeg"


def test_microphone_rejects_frames_from_another_session():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    microphone = robot.microphone.open(queue_size=2)

    transport.binary_callback(BinaryFrame(FRAME_AUDIO, 0, microphone.id + 1, 1, b"stale"))
    transport.binary_callback(BinaryFrame(FRAME_AUDIO, 0, microphone.id, 2, b"current"))

    assert microphone.read(timeout=0).data == b"current"


def test_camera_waits_for_the_acknowledged_session_frame():
    class SessionCameraTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            response = super().send_command(message_type, data, timeout)
            if message_type == "ctrl.camera.capture":
                self.binary_callback(BinaryFrame(FRAME_IMAGE, FLAG_FIRST | FLAG_LAST, 99, 1, b"stale"))
                expected_session = response["data"]["session_id"]
                threading.Timer(
                    0.01,
                    lambda: self.binary_callback(
                        BinaryFrame(FRAME_IMAGE, FLAG_FIRST | FLAG_LAST, expected_session, 2, b"current")
                    ),
                ).start()
            return response

    robot = WatcheRobot._from_transport(SessionCameraTransport())

    image = robot.camera.capture(timeout=0.5)

    assert image.data == b"current"
    assert image.session_id == 100


def test_camera_retries_transient_busy_until_capture_timeout():
    class BusyOnceCameraTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.capture_attempts = 0

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.camera.capture":
                self.capture_attempts += 1
                if self.capture_attempts == 1:
                    raise CommandError(message_type, "busy")
            response = super().send_command(message_type, data, timeout)
            if message_type == "ctrl.camera.capture":
                session_id = response["data"]["session_id"]
                threading.Timer(
                    0.01,
                    lambda: self.binary_callback(
                        BinaryFrame(FRAME_IMAGE, FLAG_FIRST | FLAG_LAST, session_id, 1, b"jpeg")
                    ),
                ).start()
            return response

    transport = BusyOnceCameraTransport()
    robot = WatcheRobot._from_transport(transport)

    assert robot.camera.capture(timeout=0.5).data == b"jpeg"
    assert transport.capture_attempts == 2


def test_camera_reports_command_ack_timeout_with_context():
    class TimeoutCameraTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.camera.capture":
                raise TimeoutError()
            return super().send_command(message_type, data, timeout)

    robot = WatcheRobot._from_transport(TimeoutCameraTransport())

    try:
        robot.camera.capture(timeout=0.01)
    except TimeoutError as error:
        assert "capture command was not acknowledged" in str(error)
    else:
        raise AssertionError("camera command timeout should include capture context")
