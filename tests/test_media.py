import pytest

from watcherobot.media import MicrophoneSession


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

