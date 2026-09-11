import pytest

from tests.test_vision import FakeTransport
from watcherobot.robot import WatcheRobot


class InferenceTransport(FakeTransport):
    def __init__(self):
        super().__init__({})
        self.capabilities += ("vision.models.v1", "vision.inference.v1")
        self.current = 0

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, dict(data), timeout))
        response = {"type": message_type, **data}
        if message_type == "ctrl.vision.models.get":
            response["models"] = [{"model_id": n, "model_name": name, "task": "detection",
                                   "contains_face_class": n == 4, "verified": True}
                                  for n, name in enumerate(("Person", "Pet", "Gesture", "Face"), 1)]
        elif message_type == "ctrl.vision.inference.start":
            self.current = data["session_id"]
        elif message_type == "ctrl.vision.inference.result.get":
            response.update(ready=True, model_id=2, sequence=5, timestamp_ms=123,
                            frame_width=320, frame_height=240, task="detection", boxes=[
                                {"x": 160, "y": 120, "width": 40, "height": 20, "score": 85, "target": 1}])
        elif message_type == "ctrl.vision.inference.stop" and self.current == data["session_id"]:
            self.current = 0
        return {"type": "sys.ack", "code": 0, "data": response}


def test_catalog_is_typed_and_does_not_select_models():
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    models = robot.vision.models()
    assert [m.model_id for m in models] == [1, 2, 3, 4]
    assert all(m.task == "detection" for m in models)
    assert [m.contains_face_class for m in models] == [False, False, False, True]
    assert len(transport.commands) == 1


def test_preview_requires_capability_and_returns_same_result_image():
    import base64
    from watcherobot import WatcheRobotError
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    with pytest.raises(WatcheRobotError):
        robot.vision.start_inference(2, preview=True)
    assert not transport.commands
    transport.capabilities += ("vision.inference.preview.v1",)
    robot = WatcheRobot._from_transport(transport)
    original = transport.send_command
    def response(message_type, data, timeout=None):
        result = original(message_type, data, timeout)
        if message_type.endswith("result.get"):
            result["data"]["jpeg_base64"] = base64.b64encode(b"\xff\xd8image\xff\xd9").decode()
        return result
    transport.send_command = response
    with robot.vision.start_inference(2, preview=True) as session:
        assert transport.commands[-1][1]["preview"] is True
        result = session.latest()
        assert result.jpeg == b"\xff\xd8image\xff\xd9"
        assert result.boxes[0].center == (160, 120)


@pytest.mark.parametrize("encoded", [None, "!invalid", "a" * 175001, "bm90LWEtanBlZw=="],
                         ids=["missing", "invalid-base64", "oversized", "not-jpeg"])
def test_preview_rejects_missing_oversized_or_invalid_image(encoded):
    from watcherobot import WatcheRobotError
    transport = InferenceTransport()
    transport.capabilities += ("vision.inference.preview.v1",)
    robot = WatcheRobot._from_transport(transport)
    original = transport.send_command
    def response(message_type, data, timeout=None):
        result = original(message_type, data, timeout)
        if message_type.endswith("result.get"):
            result["data"]["jpeg_base64"] = encoded
        return result
    transport.send_command = response
    with robot.vision.start_inference(2, preview=True) as session:
        with pytest.raises(WatcheRobotError):
            session.latest()


def test_inference_has_session_scoped_results_and_close():
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    with robot.vision.start_inference(2) as session:
        assert transport.current == session.id
        result = session.latest()
        assert result.model_id == 2 and result.sequence == 5
        assert result.boxes[0].center == (160, 120)
        assert result.boxes[0].target == 1
    assert transport.current == 0
    calls = len(transport.commands)
    session.close()
    assert len(transport.commands) == calls
    with pytest.raises(RuntimeError, match="closed"):
        session.latest()


@pytest.mark.parametrize("model", [0, 256, True, "1"])
def test_invalid_model_does_not_send(model):
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    with pytest.raises(ValueError):
        robot.vision.start_inference(model)
    assert not transport.commands


def test_robot_close_releases_inference_session():
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(3)
    robot.close()
    assert session.closed and transport.current == 0


def test_stop_failure_can_be_retried():
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(1)
    original = transport.send_command
    def failed(message_type, data, timeout=None):
        raise TimeoutError("stop timeout")
    transport.send_command = failed
    with pytest.raises(TimeoutError):
        session.close()
    assert not session.closed
    transport.send_command = original
    session.close()
    assert session.closed



@pytest.mark.parametrize("field,value", [("session_id", 99), ("model_id", 4), ("task", "classification"),
                                         ("frame_width", True), ("sequence", 0)])
def test_rejects_foreign_or_malformed_results(field, value):
    from watcherobot import WatcheRobotError
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(2)
    original = transport.send_command
    def malformed(message_type, data, timeout=None):
        response = original(message_type, data, timeout)
        response["data"][field] = value
        return response
    transport.send_command = malformed
    with pytest.raises(WatcheRobotError): session.latest()


def test_lost_start_ack_cleans_up_using_original_session_id():
    transport = InferenceTransport()
    original = transport.send_command
    def lose_ack(message_type, data, timeout=None):
        response = original(message_type, data, timeout)
        if message_type.endswith(".start"): raise TimeoutError("ACK lost")
        return response
    transport.send_command = lose_ack
    robot = WatcheRobot._from_transport(transport)
    with pytest.raises(TimeoutError): robot.vision.start_inference(1)
    assert transport.current == 0
    assert transport.commands[0][1]["session_id"] == transport.commands[1][1]["session_id"]



def test_result_iterator_skips_duplicate_snapshots_without_buffering(monkeypatch):
    monkeypatch.setattr("watcherobot.inference.time.sleep", lambda _: None)
    transport = InferenceTransport()
    original = transport.send_command
    sequences = iter([5, 5, 6])
    def snapshots(message_type, data, timeout=None):
        response = original(message_type, data, timeout)
        if message_type.endswith(".result.get"):
            response["data"]["sequence"] = next(sequences)
        return response
    transport.send_command = snapshots
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(2)
    results = session.results()
    assert next(results).sequence == 5
    assert next(results).sequence == 6
    session.close()
    with pytest.raises(StopIteration): next(results)


@pytest.mark.parametrize("method,args", [("models", ()), ("start_inference", (1,))])
def test_old_firmware_rejects_generic_api_before_sending(method, args):
    from watcherobot import WatcheRobotError
    transport = FakeTransport({}, capable=False)
    robot = WatcheRobot._from_transport(transport)
    with pytest.raises(WatcheRobotError): getattr(robot.vision, method)(*args)
    assert not transport.commands


@pytest.mark.parametrize("operation", ["start", "result"])
def test_robot_close_is_bounded_while_inference_command_waits(monkeypatch, operation):
    import threading
    import watcherobot.vision as vision
    monkeypatch.setattr(vision, "_CLOSE_CONTROL_WAIT_SECONDS", 0.03)
    transport = InferenceTransport()
    transport_closed = threading.Event()
    transport.close = transport_closed.set
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(2) if operation == "result" else None
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = transport.send_command
    errors = []
    def blocked(kind, data, timeout=None):
        if kind.endswith(".start" if operation == "start" else ".result.get"):
            entered.set()
            assert release.wait(2)
        return original(kind, data, timeout)
    transport.send_command = blocked
    def request():
        try:
            session.latest(timeout=60) if session else robot.vision.start_inference(2, timeout=60)
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=request)
    closer = threading.Thread(target=lambda: (robot.close(), finished.set()))
    worker.start()
    assert entered.wait(1)
    closer.start()
    try:
        assert finished.wait(0.5), "robot close waited for a blocked inference request"
        assert robot._closed and transport_closed.is_set()
        with pytest.raises(Exception, match="closed"):
            robot.vision.start_inference(2)
    finally:
        release.set()
        worker.join(2)
        closer.join(2)
    if operation == "start":
        assert errors, "late startup must not publish a usable session"


def test_foreign_stop_ack_keeps_session_retryable():
    from watcherobot import WatcheRobotError
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    session = robot.vision.start_inference(2)
    original = transport.send_command
    def foreign(kind, data, timeout=None):
        return {"type": "sys.ack", "code": 0, "data": {"type": kind, "session_id": session.id + 1}}
    transport.send_command = foreign
    with pytest.raises(WatcheRobotError):
        session.close()
    assert not session.closed
    transport.send_command = original
    session.close()


def test_failed_start_cleanup_retains_handle_and_old_stop_preserves_new_session():
    transport = InferenceTransport()
    robot = WatcheRobot._from_transport(transport)
    original = transport.send_command
    def failed(kind, data, timeout=None):
        if kind.endswith(".start"):
            original(kind, data, timeout)
        raise TimeoutError("ACK unavailable")
    transport.send_command = failed
    with pytest.raises(TimeoutError):
        robot.vision.start_inference(1)
    old = robot.vision._inference_session
    assert old is not None and not old.closed
    transport.send_command = original
    robot.vision.stop_inference()
    new = robot.vision.start_inference(2)
    old.close()
    original("ctrl.vision.inference.stop", {"session_id": old.id})
    assert transport.current == new.id
    robot.close()


def test_disconnected_inference_cannot_resume_or_reach_a_new_connection():
    from watcherobot import WatcheRobotError
    old_transport = InferenceTransport()
    old_robot = WatcheRobot._from_transport(old_transport)
    old_session = old_robot.vision.start_inference(2)
    old_transport.disconnect_callback()
    with pytest.raises(WatcheRobotError, match="closed"):
        old_session.latest()
    with pytest.raises(WatcheRobotError, match="closed"):
        old_robot.vision.start_inference(2)
    new_transport = InferenceTransport()
    with WatcheRobot._from_transport(new_transport) as new_robot:
        new_session = new_robot.vision.start_inference(2)
        with pytest.raises(WatcheRobotError, match="closed"):
            old_session.close()
        assert new_transport.current == new_session.id
    assert new_session.closed and new_transport.current == 0
