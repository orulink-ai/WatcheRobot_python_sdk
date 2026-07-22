import hashlib
import threading
from concurrent.futures import Future

import pytest

from watcherobot import Job, WatcheRobot
from watcherobot.errors import CommandError, WatcheRobotError
from watcherobot.protocol import (
    FLAG_FIRST,
    FLAG_FRAGMENT,
    FLAG_LAST,
    FRAME_AUDIO,
    FRAME_IMAGE,
    BinaryFrame,
)


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.message_callback = None
        self.binary_callback = None
        self.disconnect_callback = None
        self.capabilities = (
            "behavior",
            "animation",
            "display.text",
            "display.text.overlay",
            "display.text.zh_cn",
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

    def send_command_nowait(self, message_type, data):
        self.commands.append((message_type, data))
        future = Future()
        future.set_result({"type": "sys.ack", "code": 0, "data": {}})
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


def test_robot_supports_negotiated_capabilities():
    robot = WatcheRobot._from_transport(FakeTransport())

    assert robot.supports("camera.capture")
    assert not robot.supports("video.stream")
    with pytest.raises(ValueError, match="capability must be a non-empty string"):
        robot.supports("")


def test_display_show_text_builds_page_command_with_normalized_style():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    robot.display.show_text(
        "你好，WatcheRobot！",
        mode="page",
        size=24,
        color="#aabbcc",
        background="#010203",
        align="left",
        wrap=False,
    )

    assert transport.commands == [
        (
            "ctrl.display.text.set",
            {
                "text": "你好，WatcheRobot！",
                "mode": "page",
                "size": 24,
                "color": "#AABBCC",
                "background": "#010203",
                "align": "left",
                "wrap": False,
            },
        )
    ]


def test_display_show_text_uses_documented_defaults_and_clear_is_immediate():
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    robot.display.show_text("Ready")
    robot.display.clear()

    assert transport.commands == [
        (
            "ctrl.display.text.set",
            {
                "text": "Ready",
                "mode": "page",
                "size": 24,
                "color": "#FFFFFF",
                "background": "#000000",
                "align": "center",
                "wrap": True,
            },
        ),
        ("ctrl.display.clear", {}),
    ]


def test_display_requires_negotiated_capability_before_sending():
    transport = FakeTransport()
    transport.capabilities = tuple(
        capability for capability in transport.capabilities if not capability.startswith("display.text")
    )
    robot = WatcheRobot._from_transport(transport)

    with pytest.raises(WatcheRobotError, match="display.text"):
        robot.display.show_text("Ready")

    assert transport.commands == []


def test_display_overlay_and_chinese_require_their_specific_capabilities():
    transport = FakeTransport()
    transport.capabilities = ("display.text",)
    robot = WatcheRobot._from_transport(transport)

    with pytest.raises(WatcheRobotError, match="display.text.overlay"):
        robot.display.show_text("Ready", mode="overlay")
    with pytest.raises(WatcheRobotError, match="display.text.zh_cn"):
        robot.display.show_text("你好")

    assert transport.commands == []


@pytest.mark.parametrize(
    ("text", "kwargs", "message"),
    [
        ("", {}, "text must not be empty"),
        ("a" * 513, {}, "at most 512 UTF-8 bytes"),
        ("a" * 129, {}, "at most 128 Unicode characters"),
        ("bad\ttext", {}, "only newline control characters"),
        ("bad\u0085text", {}, "only newline control characters"),
        ("Ready", {"mode": "canvas"}, "mode must be"),
        ("Ready", {"mode": []}, "mode must be"),
        ("Ready", {"size": 20}, "size must be"),
        ("Ready", {"color": "white"}, "color must use #RRGGBB"),
        ("Ready", {"background": "#12"}, "background must use #RRGGBB"),
        ("Ready", {"align": "justify"}, "align must be"),
        ("Ready", {"align": []}, "align must be"),
        ("Ready", {"wrap": 1}, "wrap must be a boolean"),
    ],
)
def test_display_rejects_invalid_text_and_style(text, kwargs, message):
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    with pytest.raises(ValueError, match=message):
        robot.display.show_text(text, **kwargs)

    assert transport.commands == []


def test_display_propagates_structured_device_nack():
    class RejectingDisplayTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.display.text.set":
                raise CommandError(message_type, "text_too_long")
            return super().send_command(message_type, data, timeout)

    robot = WatcheRobot._from_transport(RejectingDisplayTransport())

    with pytest.raises(CommandError) as error:
        robot.display.show_text("Ready")

    assert error.value.reason == "text_too_long"


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
    class PendingAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

    transport = PendingAudioTransport()
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
    assert playback.reason == "playback_write_failed"
    assert transport.future.cancelled()


def test_rejected_audio_stop_keeps_the_host_sender_alive():
    class RejectingStopTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.audio.stop":
                raise CommandError(message_type, "busy")
            return super().send_command(message_type, data, timeout)

    transport = RejectingStopTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    with pytest.raises(CommandError, match="busy"):
        robot.audio.stop()

    assert not transport.future.cancelled()
    assert playback.state.value == "starting"


def test_audio_sender_failure_stops_device_and_releases_current_playback():
    class FailingAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

    transport = FailingAudioTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    transport.future.set_exception(OSError("socket write failed"))

    assert playback.state.value == "failed"
    assert playback.reason == "audio_send_failed"
    assert robot._audio_playback is None
    assert robot._audio_send_future is None
    assert transport.commands[-1] == ("ctrl.audio.stop", {})


def test_audio_sender_start_failure_stops_the_authorized_device_stream():
    class BrokenAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            raise OSError("cannot schedule sender")

    transport = BrokenAudioTransport()
    robot = WatcheRobot._from_transport(transport)

    with pytest.raises(OSError, match="cannot schedule sender"):
        robot.audio.play_pcm(b"\x01\x00")

    assert robot._audio_playback is None
    assert transport.commands[-1] == ("ctrl.audio.stop", {})


def test_rejected_installed_sound_keeps_existing_host_sender_alive():
    class RejectingAudioTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.audio.play":
                raise CommandError(message_type, "not_found")
            return super().send_command(message_type, data, timeout)

    transport = RejectingAudioTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    with pytest.raises(CommandError, match="not_found"):
        robot.audio.play("missing")

    assert not transport.future.cancelled()
    assert playback.state.value == "starting"


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


def test_rejected_audio_playback_cancel_keeps_sender_and_playback_active():
    class RejectingStopTransport(FakeTransport):
        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            self.future = Future()
            return self.future

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.audio.stop":
                raise CommandError(message_type, "busy")
            return super().send_command(message_type, data, timeout)

    transport = RejectingStopTransport()
    robot = WatcheRobot._from_transport(transport)
    playback = robot.audio.play_pcm(b"\x01\x00")

    with pytest.raises(CommandError, match="busy"):
        playback.cancel()

    assert not transport.future.cancelled()
    assert playback.state.value == "starting"
    assert robot._audio_playback is playback


def test_old_sender_failure_during_successful_replacement_does_not_stop_new_stream():
    class BlockingReplacementTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.begin_count = 0
            self.replacement_started = threading.Event()
            self.release_replacement = threading.Event()
            self.futures = []

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.audio.stream.begin":
                self.begin_count += 1
                if self.begin_count == 2:
                    self.replacement_started.set()
                    assert self.release_replacement.wait(1)
            return super().send_command(message_type, data, timeout)

        def send_audio_stream(self, pcm, *, stream_id, chunk_bytes=4096):
            future = Future()
            self.futures.append(future)
            return future

    transport = BlockingReplacementTransport()
    robot = WatcheRobot._from_transport(transport)
    first = robot.audio.play_pcm(b"\x01\x00")
    replacements = []
    errors = []

    def replace_audio():
        try:
            replacements.append(robot.audio.play_pcm(b"\x02\x00"))
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    replacement_thread = threading.Thread(target=replace_audio)
    replacement_thread.start()
    assert transport.replacement_started.wait(1)
    transport.futures[0].set_exception(OSError("old sender failed"))
    transport.release_replacement.set()
    replacement_thread.join(1)

    assert not replacement_thread.is_alive()
    assert errors == []
    assert first.state.value == "failed"
    assert len(replacements) == 1
    assert replacements[0].state.value == "starting"
    assert [command[0] for command in transport.commands] == [
        "ctrl.audio.stream.begin",
        "ctrl.audio.stream.begin",
    ]


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
    assert job.reason == "disconnected"
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
    assert robot.camera.capture(timeout=0.1).data == b"jpeg"


@pytest.mark.parametrize("timeout", [0, -1])
def test_camera_rejects_non_positive_timeout(timeout):
    robot = WatcheRobot._from_transport(FakeTransport())

    with pytest.raises(ValueError, match="timeout must be positive"):
        robot.camera.capture(timeout=timeout)


def test_camera_reassembles_fragmented_jpeg_before_returning_it():
    class FragmentedCameraTransport(FakeTransport):
        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.camera.capture":
                self.binary_callback(
                    BinaryFrame(FRAME_IMAGE, FLAG_FIRST | FLAG_FRAGMENT, 0, 10, b"jpeg-")
                )
                self.binary_callback(BinaryFrame(FRAME_IMAGE, FLAG_FRAGMENT, 0, 11, b"middle-"))
                self.binary_callback(BinaryFrame(FRAME_IMAGE, FLAG_FRAGMENT, 0, 12, b"end"))
                self.binary_callback(
                    BinaryFrame(FRAME_IMAGE, FLAG_LAST | FLAG_FRAGMENT, 0, 13, b"")
                )
            return super().send_command(message_type, data, timeout)

    robot = WatcheRobot._from_transport(FragmentedCameraTransport())

    image = robot.camera.capture(timeout=0.1)

    assert image.data == b"jpeg-middle-end"
    assert image.sequence == 13


def test_concurrent_microphone_open_is_rejected_before_a_second_command():
    class BlockingMicrophoneTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.open_started = threading.Event()
            self.release_open = threading.Event()

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.microphone.open":
                self.open_started.set()
                assert self.release_open.wait(1)
            return super().send_command(message_type, data, timeout)

    transport = BlockingMicrophoneTransport()
    robot = WatcheRobot._from_transport(transport)
    opened = []
    opener = threading.Thread(target=lambda: opened.append(robot.microphone.open()))
    opener.start()
    assert transport.open_started.wait(1)

    with pytest.raises(WatcheRobotError, match="microphone session is already open or opening"):
        robot.microphone.open()

    transport.release_open.set()
    opener.join(1)
    assert not opener.is_alive()
    assert len(opened) == 1
    assert [command[0] for command in transport.commands].count("ctrl.microphone.open") == 1


def test_microphone_open_does_not_publish_a_session_after_robot_close():
    class BlockingMicrophoneTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.open_started = threading.Event()
            self.release_open = threading.Event()

        def send_command(self, message_type, data, timeout=None):
            if message_type == "ctrl.microphone.open":
                self.open_started.set()
                assert self.release_open.wait(1)
            return super().send_command(message_type, data, timeout)

    transport = BlockingMicrophoneTransport()
    robot = WatcheRobot._from_transport(transport)
    opened = []
    errors = []

    def open_microphone():
        try:
            opened.append(robot.microphone.open())
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    opener = threading.Thread(target=open_microphone)
    opener.start()
    assert transport.open_started.wait(1)
    robot.close()
    transport.release_open.set()
    opener.join(1)

    assert not opener.is_alive()
    assert opened == []
    assert len(errors) == 1
    assert isinstance(errors[0], WatcheRobotError)
    assert "closed while opening microphone" in str(errors[0])
    assert robot._microphone is None
    assert not robot._microphone_opening


def test_microphone_open_rejects_an_already_closed_robot():
    robot = WatcheRobot._from_transport(FakeTransport())
    robot.close()

    with pytest.raises(WatcheRobotError, match="robot connection is closed"):
        robot.microphone.open()


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
