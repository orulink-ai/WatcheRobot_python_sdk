import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from watcherobot.media import AudioFormat, AudioRecording, ImageFrame, MicrophoneSession
from watcherobot.robot import MicrophoneDomain


class FakeRobot:
    def __init__(self):
        self.closed_sessions = []

    def _close_microphone(self, session_id):
        self.closed_sessions.append(session_id)


def test_microphone_queue_drops_oldest_frame_and_counts_it():
    session = MicrophoneSession(FakeRobot(), session_id=8, queue_size=2)

    session._push(b"one", sequence=1)
    session._push(b"two", sequence=2)
    session._push(b"three", sequence=3)

    assert session.dropped_frames == 1
    assert session.read(timeout=0).data == b"two"
    assert session.read(timeout=0).data == b"three"


def test_microphone_context_closes_session_once():
    robot = FakeRobot()
    session = MicrophoneSession(robot, session_id=12)

    with session as opened:
        assert opened.format.sample_rate_hz == 16000
        assert opened.format.channels == 1
        assert opened.format.sample_width_bytes == 2
    session.close()

    assert robot.closed_sessions == [12]


def test_microphone_read_timeout_is_plain_timeout_error():
    with pytest.raises(TimeoutError):
        MicrophoneSession(FakeRobot(), session_id=2).read(timeout=0)


def test_image_frame_save_creates_parent_and_writes_jpeg(tmp_path: Path):
    output = tmp_path / "nested" / "camera.jpg"
    image = ImageFrame(data=b"\xff\xd8jpeg\xff\xd9", sequence=1, timestamp=1.0)

    saved = image.save(output)

    assert saved == output
    assert output.read_bytes() == image.data


def test_audio_recording_save_writes_standard_wave(tmp_path: Path):
    output = tmp_path / "nested" / "microphone.wav"
    recording = AudioRecording(
        data=b"\x01\x00\x02\x00",
        format=AudioFormat(sample_rate_hz=16000, channels=1, sample_width_bytes=2),
        dropped_frames=3,
    )

    saved = recording.save(output)

    assert saved == output
    assert recording.duration_seconds == pytest.approx(0.000125)
    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.readframes(wav_file.getnframes()) == recording.data


def test_microphone_domain_record_returns_exact_duration():
    class FakeSession:
        format = AudioFormat(sample_rate_hz=16000, channels=1, sample_width_bytes=2)
        dropped_frames = 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, timeout):
            assert timeout > 0
            return SimpleNamespace(data=b"\x01\x00\x02\x00\x03\x00")

    robot = SimpleNamespace(_open_microphone=lambda queue_size: FakeSession())

    recording = MicrophoneDomain(robot).record(duration=0.000125, timeout=1.0)

    assert recording.data == b"\x01\x00\x02\x00"
    assert recording.format.sample_rate_hz == 16000
    assert recording.dropped_frames == 2


def test_microphone_domain_record_rejects_invalid_duration():
    robot = SimpleNamespace(_open_microphone=lambda queue_size: None)

    with pytest.raises(ValueError, match="duration must be positive"):
        MicrophoneDomain(robot).record(duration=0)
