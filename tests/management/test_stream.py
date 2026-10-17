import io
import json
import threading

from watcherobot.management.stream import serve


def test_long_operation_does_not_block_status_and_frames_keep_request_identity():
    status_seen = threading.Event()

    def dispatch(request, output):
        if request["action"] == "update":
            output({"type": "progress", "phase": "downloading"})
            assert status_seen.wait(2)
        else:
            status_seen.set()
        output({"type": "result", "ok": True, "data": {"action": request["action"]}})
        return 0

    incoming = io.BytesIO(
        b'{"id":"a","request":{"action":"update"}}\n{"id":"b","request":{"action":"status"}}\n'
    )
    outgoing = io.StringIO()
    assert serve(incoming, outgoing, dispatch=dispatch) == 0
    frames = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert {
        (f["id"], f["data"]["action"]) for f in frames if f["type"] == "result"
    } == {("a", "update"), ("b", "status")}
    assert next(f for f in frames if f["type"] == "progress")["id"] == "a"


def test_bad_frame_does_not_prevent_later_valid_requests():
    incoming = io.BytesIO(
        b'{"id":"bad","request":null}\n{"id":"good","request":{"action":"status"}}\n'
    )
    outgoing = io.StringIO()

    def dispatch(request, output):
        output({"type": "result", "ok": True, "data": {}})
        return 0

    assert serve(incoming, outgoing, dispatch=dispatch) == 0
    frames = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert next(f for f in frames if f["id"] == "bad")["ok"] is False
    assert next(f for f in frames if f["id"] == "good")["ok"] is True
