import wave
from pathlib import Path
from threading import Event, Thread
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


def test_microphone_session_decodes_opus_before_exposing_audio_frames():
    class FakeDecoder:
        def decode(self, packet: bytes) -> bytes:
            assert packet == b"opus-packet"
            return b"\x01\x00\x02\x00"

        def flush(self) -> bytes:
            return b""

    session = MicrophoneSession(FakeRobot(), session_id=2, decoder=FakeDecoder())

    session._push_opus(b"opus-packet", sequence=9)

    frame = session.read(timeout=0)
    assert frame.data == b"\x01\x00\x02\x00"
    assert frame.sequence == 9


def test_microphone_close_keeps_a_packet_already_being_decoded():
    decode_started = Event()
    release_decode = Event()

    class BlockingDecoder:
        def decode(self, _packet: bytes) -> bytes:
            decode_started.set()
            assert release_decode.wait(1)
            return b"\x01\x00\x02\x00"

        def flush(self) -> bytes:
            return b""

    session = MicrophoneSession(FakeRobot(), session_id=2, decoder=BlockingDecoder())
    worker = Thread(target=lambda: session._push_opus(b"opus-packet", sequence=9))
    worker.start()
    assert decode_started.wait(1)

    closer = Thread(target=session.close)
    closer.start()
    release_decode.set()
    worker.join(1)
    closer.join(1)

    assert not worker.is_alive()
    assert not closer.is_alive()
    assert session.read(timeout=0).data == b"\x01\x00\x02\x00"


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

    robot = SimpleNamespace(
        _open_microphone=lambda *, queue_size, decode_opus: FakeSession()
    )

    recording = MicrophoneDomain(robot).record_pcm(duration=0.000125, timeout=1.0)

    assert recording.data == b"\x01\x00\x02\x00"
    assert recording.format.sample_rate_hz == 16000
    assert recording.dropped_frames == 2


def test_microphone_domain_open_keeps_raw_opus_separate_from_pcm():
    calls: list[bool] = []
    raw_session = object()
    pcm_session = object()

    def open_microphone(*, queue_size: int, decode_opus: bool):
        assert queue_size == 32
        calls.append(decode_opus)
        return pcm_session if decode_opus else raw_session

    robot = SimpleNamespace(_open_microphone=open_microphone)

    assert MicrophoneDomain(robot).open() is raw_session
    assert MicrophoneDomain(robot).open_pcm() is pcm_session
    assert calls == [False, True]


def test_microphone_domain_record_keeps_raw_opus_separate_from_pcm():
    class FakeSession:
        format = AudioFormat(sample_rate_hz=16000, channels=1, sample_width_bytes=2)
        dropped_frames = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, timeout):
            assert timeout > 0
            return SimpleNamespace(data=b"\x01\x02\x03\x04")

    calls: list[bool] = []

    def open_microphone(*, queue_size: int, decode_opus: bool):
        assert queue_size == 32
        calls.append(decode_opus)
        return FakeSession()

    domain = MicrophoneDomain(SimpleNamespace(_open_microphone=open_microphone))

    domain.record(duration=0.000125, timeout=1.0)
    domain.record_pcm(duration=0.000125, timeout=1.0)

    assert calls == [False, True]


def test_microphone_domain_record_rejects_invalid_duration():
    robot = SimpleNamespace(_open_microphone=lambda queue_size: None)

    with pytest.raises(ValueError, match="duration must be positive"):
        MicrophoneDomain(robot).record(duration=0)
