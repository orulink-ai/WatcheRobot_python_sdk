import struct
import threading
from types import SimpleNamespace

from meeting.config import Settings
from meeting.robot import SDKRobot


def test_recording_closes_microphone_before_returning_and_reports_frames():
    class Session:
        dropped_frames = decode_failures = 0
        closed = False
        def __init__(self):
            self.frames = iter([struct.pack('<h', 2000) * 960] * 4 + [bytes(1920)] * 6)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
        def read(self, timeout):
            return SimpleNamespace(data=next(self.frames))
    session = Session()
    sdk = SimpleNamespace(microphone=SimpleNamespace(open_pcm=lambda **kw: session))
    robot = SDKRobot(sdk, threading.Event())
    audio = robot._record_utterance(Settings(silence_seconds=0.3), threading.Event())
    assert len(audio) > 0
    assert session.closed
    assert robot.microphone_stats['frames'] == 9
    assert robot.microphone_stats['peak_rms'] == 2000
    assert robot.microphone_stats['decode_failures'] == 0
    assert robot.microphone_stats['speech_detected']
    assert robot.microphone_stats['end_reason'] == 'silence'
    assert robot.microphone_stats['state'] == 'closed'
