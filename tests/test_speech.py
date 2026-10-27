import threading

import pytest

from watcherobot.errors import WatcheRobotError
from watcherobot.errors import CommandError
from watcherobot.robot import WatcheRobot


class SpeechTransport:
    capabilities = ("speech.joyinside.v1",)
    device_info = {"device_id": "speech-test"}

    def __init__(self):
        self.commands = []
        self.auto_results = True

    def set_callbacks(self, message, binary, disconnect):
        self.message = message
        self.disconnect = disconnect

    def event(self, session_id, kind, **data):
        self.message({"type": "evt.speech", "data": {
            "session_id": session_id, "kind": kind, **data}})

    def send_command(self, kind, data, timeout=None):
        self.commands.append((kind, data))
        if kind == "ctrl.speech.open":
            self.event(data["session_id"], "ready", recognition_mode="conversation_asr")
        elif kind == "ctrl.speech.close":
            self.event(data["session_id"], "closed", reason="requested")
        elif self.auto_results and kind == "ctrl.speech.recognize":
            self.event(data["session_id"], "recognition", request_id=data["request_id"], text="你好 Pi")
        elif self.auto_results and kind == "ctrl.speech.say":
            self.event(data["session_id"], "playback", request_id=data["request_id"], state="completed")
        return {"data": {"session_id": data.get("session_id")}}

    def close(self):
        pass


def make_robot():
    transport = SpeechTransport()
    return WatcheRobot._from_transport(transport), transport


def test_open_negotiates_capability_and_handles_ready_before_ack():
    robot, transport = make_robot()
    with robot.speech.open(timeout=1) as speech:
        assert speech.recognition_mode == "conversation_asr"
        assert speech.recognize(timeout=1) == "你好 Pi"
        speech.say("这是我自己的回复。", timeout=1)
    assert [kind for kind, _ in transport.commands] == [
        "ctrl.speech.open", "ctrl.speech.recognize", "ctrl.speech.say", "ctrl.speech.close"]


def test_unsupported_firmware_does_not_send_commands():
    robot, transport = make_robot()
    transport.capabilities = ()
    with pytest.raises(WatcheRobotError, match="speech.joyinside.v1"):
        robot.speech.open()
    assert transport.commands == []


def test_one_speech_session_per_robot():
    robot, _ = make_robot()
    with robot.speech.open():
        with pytest.raises(WatcheRobotError, match="already"):
            robot.speech.open()


@pytest.mark.parametrize("text", ["", "   ", "你" * 334, "a" * 1000, "hi\0there"])
def test_say_validates_utf8_bytes_before_sending(text):
    robot, transport = make_robot()
    with robot.speech.open() as speech:
        with pytest.raises(ValueError):
            speech.say(text)
        assert not any(kind == "ctrl.speech.say" for kind, _ in transport.commands)


def test_result_timeout_cancels_only_matching_request():
    robot, transport = make_robot()
    transport.auto_results = False
    with robot.speech.open() as speech:
        with pytest.raises(TimeoutError):
            speech.recognize(timeout=0.02)
        assert transport.commands[-1][0] == "ctrl.speech.cancel"
        assert transport.commands[-1][1]["request_id"] == transport.commands[-2][1]["request_id"]


def test_foreign_late_and_partial_events_do_not_complete_recognition():
    robot, transport = make_robot()
    transport.auto_results = False
    with robot.speech.open() as speech:
        original = transport.send_command

        def send(kind, data, timeout=None):
            result = original(kind, data, timeout)
            if kind == "ctrl.speech.recognize":
                transport.event("foreign", "recognition", request_id=data["request_id"], text="wrong")
                transport.event(speech.id, "recognition", request_id="retired", text="wrong")
                transport.event(speech.id, "recognition", request_id=data["request_id"], text=42)
                transport.event(speech.id, "recognition", request_id=data["request_id"], text="correct")
            return result

        transport.send_command = send
        assert speech.recognize(timeout=1) == "correct"


def test_disconnect_wakes_waiter_and_does_not_send_to_new_device():
    robot, transport = make_robot()
    transport.auto_results = False
    speech = robot.speech.open()
    finished = threading.Event()
    errors = []

    def wait():
        try:
            speech.recognize(timeout=10)
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=wait)
    worker.start()
    transport.event(speech.id, "closed", reason="device_disconnected")
    assert finished.wait(1)
    worker.join()
    assert isinstance(errors[0], WatcheRobotError)


def test_failed_playback_is_not_reported_as_success():
    robot, transport = make_robot()
    with robot.speech.open() as speech:
        original = transport.send_command

        def send(kind, data, timeout=None):
            if kind == "ctrl.speech.say":
                transport.event(speech.id, "error", request_id=data["request_id"], reason="audio_write_failed")
                return {"data": {"session_id": speech.id}}
            return original(kind, data, timeout)

        transport.send_command = send
        with pytest.raises(WatcheRobotError, match="audio_write_failed"):
            speech.say("test", timeout=1)


def test_rejected_open_does_not_leave_a_phantom_session():
    robot, transport = make_robot()
    original = transport.send_command

    def reject(kind, data, timeout=None):
        raise CommandError(kind, "joyinside_not_configured")

    transport.send_command = reject
    with pytest.raises(CommandError):
        robot.speech.open(timeout=0.1)
    transport.send_command = original
    with robot.speech.open(timeout=1):
        pass


def test_closed_event_wins_over_close_command_nack():
    robot, transport = make_robot()
    speech = robot.speech.open()
    original = transport.send_command

    def close_race(kind, data, timeout=None):
        if kind == "ctrl.speech.close":
            transport.event(speech.id, "closed", reason="closed")
            raise CommandError(kind, "session_mismatch")
        return original(kind, data, timeout)

    transport.send_command = close_race
    speech.close(timeout=1)
    speech.close(timeout=1)


def test_close_mismatch_retires_only_the_unowned_local_handle():
    robot, transport = make_robot()
    speech = robot.speech.open()
    original = transport.send_command

    def no_longer_owned(kind, data, timeout=None):
        if kind == "ctrl.speech.close":
            raise CommandError(kind, "session_mismatch")
        return original(kind, data, timeout)

    transport.send_command = no_longer_owned
    speech.close(timeout=0.1)
    assert robot.speech._session is None
    assert [kind for kind, _ in transport.commands] == ["ctrl.speech.open"]
    transport.send_command = original
    with robot.speech.open(timeout=1):
        pass


@pytest.mark.parametrize("reason", ["busy", "audio_release_failed"])
def test_other_close_rejections_retain_the_owner_for_retry(reason):
    robot, transport = make_robot()
    speech = robot.speech.open()
    original = transport.send_command

    def reject(kind, data, timeout=None):
        if kind == "ctrl.speech.close":
            raise CommandError(kind, reason)
        return original(kind, data, timeout)

    transport.send_command = reject
    with pytest.raises(CommandError, match=reason):
        speech.close(timeout=0.1)
    assert robot.speech._session is speech
    transport.send_command = original
    speech.close(timeout=1)
