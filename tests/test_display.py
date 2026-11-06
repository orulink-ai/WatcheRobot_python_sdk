"""Display stream contract tests; no device, daemon, or serial port required."""
from concurrent.futures import Future
import importlib
import struct
import threading
import time

import pytest

from watcherobot.errors import CommandError, WatcheRobotError


JPEG = b"\xff\xd8payload\xff\xd9"


class FakeTransport:
    def __init__(self):
        self.packets = []
        self.futures = []
        self.on_send = None
        self.complete_send = True
        self.send_error = None
        self.sent = threading.Event()

    def send_device(self, packet, *, admit=None):
        if admit is not None:
            admit()
        self.packets.append(packet)
        future = Future()
        self.futures.append(future)
        self.sent.set()
        if self.on_send is not None:
            self.on_send(packet)
        if self.complete_send:
            if self.send_error is not None:
                future.set_exception(self.send_error)
            else:
                future.set_result(None)
        return future


class FakeRobot:
    def __init__(self):
        self._transport = FakeTransport()
        self.commands = []
        self.capabilities = []
        self.supported = True
        self.open_ack = {"type": "sys.ack", "code": 0, "data": {"command_id": "open-1",
            "stream_id": 41, "max_frame_bytes": 65536, "lease_ms": 5000,
        }}
        self.on_command = None

    def _require_capability(self, capability):
        self.capabilities.append(capability)
        if not self.supported:
            raise WatcheRobotError("missing " + capability)

    def _command_guarded(self, kind, data, *, timeout, admit):
        admit()
        return self._command(kind, data, timeout=timeout)

    def _command(self, kind, data, timeout=None):
        self.commands.append((kind, data.copy(), timeout))
        if self.on_command is not None:
            result = self.on_command(kind, data, timeout)
            if result is not None:
                return result
        if kind == "ctrl.display.open":
            return self.open_ack
        assert kind == "ctrl.display.close"
        return {"type": "sys.ack", "code": 0, "data": {"command_id": "close-1", "stream_id": data["stream_id"]}}


def make_domain():
    display = importlib.import_module("watcherobot.display")
    robot = FakeRobot()
    return display.DisplayDomain(robot), robot


def ack(domain, sequence=1, stream_id=41, **overrides):
    domain._on_event({
        "stream_id": stream_id, "sequence": sequence,
        "device_sequence": sequence + 100, "code": 0, **overrides,
    })


def auto_ack(domain, robot):
    def presented(packet):
        stream_id, sequence = struct.unpack_from("<HI", packet, 6)
        ack(domain, sequence, stream_id)
    robot._transport.on_send = presented


class Call:
    """Bounded worker that propagates exceptions back to the test thread."""
    def __init__(self, function):
        self.done = threading.Event()
        self.value = None
        self.error = None

        def run():
            try:
                self.value = function()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def result(self, timeout=1):
        assert self.done.wait(timeout), "SDK call did not finish within its bound"
        self.thread.join(timeout)
        if self.error is not None:
            raise self.error
        return self.value


def test_open_capability_wire_packet_sequences_and_context_cleanup():
    domain, robot = make_domain()
    auto_ack(domain, robot)
    with domain.open_stream(timeout=0.5) as session:
        assert session.stream_id == 41
        assert session.max_frame_bytes == 65536
        assert session.lease_ms == 5000
        assert session.present(JPEG) == 1
        assert session.present(JPEG, timeout=0.5) == 2
    assert robot.capabilities == ["display.stream.v1"]
    assert [(kind, data) for kind, data, _ in robot.commands] == [
        ("ctrl.display.open", {"width": 412, "height": 412, "format": "jpeg"}),
        ("ctrl.display.close", {"stream_id": 41}),
    ]
    assert robot._transport.packets == [
        b"WSPK\x08\x03\x29\x00\x01\x00\x00\x00\x0b\x00\x00\x00" + JPEG,
        b"WSPK\x08\x03\x29\x00\x02\x00\x00\x00\x0b\x00\x00\x00" + JPEG,
    ]
    assert all(0 < timeout <= 3 for _, _, timeout in robot.commands)


def test_ack_envelope_and_optional_lease_are_supported():
    domain, robot = make_domain()
    robot.open_ack = {"type": "sys.ack", "code": 0, "data": {"command_id": "open-2", "stream_id": 65535, "max_frame_bytes": 20}}
    auto_ack(domain, robot)
    with domain.open_stream() as session:
        assert session.stream_id == 65535
        assert session.lease_ms is None
        assert session.present(JPEG) == 1


def test_capability_rejection_has_no_command_or_binary_side_effect():
    domain, robot = make_domain()
    robot.supported = False
    with pytest.raises(WatcheRobotError, match="display.stream.v1"):
        domain.open_stream()
    assert robot.commands == []
    assert robot._transport.packets == []


@pytest.mark.parametrize("kwargs", [
    {"width": 0}, {"width": 411}, {"width": True}, {"width": 412.0},
    {"height": -1}, {"height": 413}, {"height": "412"},
    {"timeout": 0}, {"timeout": -1}, {"timeout": float("inf")},
    {"timeout": float("nan")}, {"timeout": True}, {"timeout": "3"},
])
def test_open_rejects_invalid_arguments_before_any_robot_call(kwargs):
    domain, robot = make_domain()
    with pytest.raises((TypeError, ValueError)):
        domain.open_stream(**kwargs)
    assert robot.capabilities == []
    assert robot.commands == []


@pytest.mark.parametrize("field,value", [
    ("stream_id", 0), ("stream_id", 65536), ("stream_id", True),
    ("stream_id", "41"), ("stream_id", None),
    ("max_frame_bytes", 0), ("max_frame_bytes", -1),
    ("max_frame_bytes", 65537), ("max_frame_bytes", True),
    ("max_frame_bytes", 65536.0), ("max_frame_bytes", None),
    ("lease_ms", 0), ("lease_ms", -1), ("lease_ms", True),
    ("lease_ms", "5000"),
])
def test_invalid_open_ack_never_sends_binary_or_closes_unowned_stream(field, value):
    domain, robot = make_domain()
    robot.open_ack["data"][field] = value
    with pytest.raises(WatcheRobotError):
        domain.open_stream(timeout=0.1)
    domain._close()
    assert [kind for kind, _, _ in robot.commands] == ["ctrl.display.open"]
    assert robot._transport.packets == []


@pytest.mark.parametrize("response", [None, [], {}, {"data": None}])
def test_missing_or_non_object_open_ack_is_rejected(response):
    domain, robot = make_domain()
    robot.open_ack = response
    with pytest.raises(WatcheRobotError):
        domain.open_stream()


@pytest.mark.parametrize("jpeg", [
    b"", b"\xff\xd8", b"not jpeg", b"\xff\xd8broken", b"broken\xff\xd9",
    bytearray(JPEG), memoryview(JPEG), "jpeg", None,
])
def test_invalid_jpeg_does_not_consume_sequence_or_poison_session(jpeg):
    domain, robot = make_domain()
    auto_ack(domain, robot)
    with domain.open_stream() as session:
        with pytest.raises((TypeError, ValueError)):
            session.present(jpeg)
        assert robot._transport.packets == []
        assert session.present(JPEG) == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_invalid_present_and_close_timeouts_have_no_side_effect(timeout):
    domain, robot = make_domain()
    auto_ack(domain, robot)
    session = domain.open_stream()
    with pytest.raises((TypeError, ValueError)):
        session.present(JPEG, timeout=timeout)
    with pytest.raises((TypeError, ValueError)):
        session.close(timeout=timeout)
    assert robot._transport.packets == []
    assert len(robot.commands) == 1
    assert session.present(JPEG) == 1
    session.close()


def test_negotiated_frame_limit_inclusive_and_global_limit():
    domain, robot = make_domain()
    robot.open_ack["data"]["max_frame_bytes"] = 12
    auto_ack(domain, robot)
    with domain.open_stream() as session:
        with pytest.raises(ValueError):
            session.present(b"\xff\xd8" + b"x" * 9 + b"\xff\xd9")
        assert robot._transport.packets == []
        assert session.present(b"\xff\xd8" + b"x" * 8 + b"\xff\xd9") == 1
    domain, robot = make_domain()
    auto_ack(domain, robot)
    with domain.open_stream() as session:
        with pytest.raises(ValueError):
            session.present(b"\xff\xd8" + b"x" * 65533 + b"\xff\xd9")
        assert session.present(b"\xff\xd8" + b"x" * 65532 + b"\xff\xd9") == 1


def test_present_waits_for_delayed_matching_ack_and_ignores_foreign_events():
    domain, robot = make_domain()
    with domain.open_stream() as session:
        call = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        ack(domain, stream_id=42)
        ack(domain, sequence=2)
        assert not call.done.wait(0.02)
        ack(domain)
        assert call.result() == 1


def test_future_and_idle_acks_are_not_retained_for_later_frames():
    domain, robot = make_domain()
    ack(domain)
    with domain.open_stream() as session:
        for sequence in range(1, 2000):
            ack(domain, sequence=sequence)
        first = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        for sequence in range(2, 2000):
            ack(domain, sequence=sequence)
        ack(domain)
        assert first.result() == 1
        robot._transport.sent.clear()
        second = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        ack(domain, sequence=1)
        assert not second.done.wait(0.02)
        ack(domain, sequence=2)
        assert second.result() == 2


@pytest.mark.parametrize("event", [
    None, [], {}, {"stream_id": 41, "sequence": 1, "code": 0},
    {"stream_id": 41, "sequence": True, "device_sequence": 1, "code": 0},
    {"stream_id": 41, "sequence": 1, "device_sequence": True, "code": 0},
    {"stream_id": 41, "sequence": 1, "device_sequence": -1, "code": 0},
    {"stream_id": 41, "sequence": 1, "device_sequence": 1, "code": False},
    {"stream_id": 41, "sequence": 1, "device_sequence": 1, "code": "0"},
])
def test_malformed_events_cannot_acknowledge_current_frame(event):
    domain, robot = make_domain()
    with domain.open_stream() as session:
        call = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        domain._on_event(event)
        assert not call.done.wait(0.01)
        ack(domain)
        assert call.result() == 1


def test_concurrent_present_is_rejected_without_poisoning_owner():
    domain, robot = make_domain()
    with domain.open_stream() as session:
        first = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        with pytest.raises(WatcheRobotError):
            session.present(JPEG, timeout=0.1)
        assert len(robot._transport.packets) == 1
        ack(domain)
        assert first.result() == 1
        auto_ack(domain, robot)
        assert session.present(JPEG) == 2


def test_ack_timeout_permanently_disables_present_even_after_late_ack():
    domain, robot = make_domain()
    session = domain.open_stream()
    with pytest.raises(TimeoutError):
        session.present(JPEG, timeout=0.02)
    ack(domain)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    with pytest.raises(WatcheRobotError):
        domain.open_stream()
    assert len(robot._transport.packets) == 1
    session.close(timeout=0.1)
    assert robot.commands[-1][:2] == ("ctrl.display.close", {"stream_id": 41})


@pytest.mark.parametrize("failed_send", [False, True])
def test_transport_send_failure_or_timeout_disables_session(failed_send):
    domain, robot = make_domain()
    session = domain.open_stream()
    robot._transport.complete_send = failed_send
    robot._transport.send_error = OSError("channel failed")
    with pytest.raises(OSError if failed_send else TimeoutError):
        session.present(JPEG, timeout=0.02)
    ack(domain)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    assert len(robot._transport.packets) == 1
    session.close()


def test_negative_presented_ack_disables_session_and_preserves_reason():
    domain, robot = make_domain()
    robot._transport.on_send = lambda _: ack(domain, code=7, reason="decode_failed")
    session = domain.open_stream()
    with pytest.raises(CommandError, match="decode_failed"):
        session.present(JPEG)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    session.close()


def test_send_and_presented_ack_share_one_deadline():
    domain, robot = make_domain()
    session = domain.open_stream()
    robot._transport.complete_send = False
    start = time.monotonic()
    call = Call(lambda: session.present(JPEG, timeout=0.18))
    assert robot._transport.sent.wait(0.5)
    assert not call.done.wait(0.10)
    robot._transport.futures[0].set_result(None)
    with pytest.raises(TimeoutError):
        call.result()
    assert time.monotonic() - start < 0.26
    session.close()


def test_early_ack_does_not_complete_before_send_future():
    domain, robot = make_domain()
    auto_ack(domain, robot)
    robot._transport.complete_send = False
    with domain.open_stream() as session:
        call = Call(lambda: session.present(JPEG, timeout=0.8))
        assert robot._transport.sent.wait(0.5)
        assert not call.done.wait(0.02)
        robot._transport.futures[0].set_result(None)
        assert call.result() == 1


@pytest.mark.parametrize("pending_send", [False, True])
def test_disconnect_wakes_waiters_even_with_running_send_without_close(pending_send):
    domain, robot = make_domain()
    session = domain.open_stream()
    robot._transport.complete_send = not pending_send
    call = Call(lambda: session.present(JPEG, timeout=1))
    assert robot._transport.sent.wait(0.5)
    if pending_send:
        # A running Future cannot be cancelled: wakeup must not depend on cancel().
        assert robot._transport.futures[0].set_running_or_notify_cancel()
    domain._disconnected()
    with pytest.raises(WatcheRobotError, match="disconnect"):
        call.result(0.25)
    session.close()
    domain._close()
    assert [kind for kind, _, _ in robot.commands] == ["ctrl.display.open"]
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)


def test_close_wakes_present_waiter_and_old_handle_cannot_close_reused_id():
    domain, robot = make_domain()
    old = domain.open_stream()
    call = Call(lambda: old.present(JPEG, timeout=1))
    assert robot._transport.sent.wait(0.5)
    old.close()
    with pytest.raises(WatcheRobotError):
        call.result(0.25)
    current = domain.open_stream()
    old.close()
    auto_ack(domain, robot)
    assert current.present(JPEG) == 1
    current.close()
    assert [kind for kind, _, _ in robot.commands].count("ctrl.display.close") == 2


def test_duplicate_open_preserves_active_owner_and_sends_no_close():
    domain, robot = make_domain()
    auto_ack(domain, robot)
    with domain.open_stream() as owner:
        with pytest.raises(WatcheRobotError):
            domain.open_stream()
        assert len(robot.commands) == 1
        assert owner.present(JPEG) == 1


def test_concurrent_open_has_one_reservation_without_command_lock_deadlock():
    domain, robot = make_domain()
    entered, release = threading.Event(), threading.Event()

    def hold_open(kind, data, timeout):
        if kind == "ctrl.display.open":
            entered.set()
            assert release.wait(0.8)
    robot.on_command = hold_open
    first = Call(lambda: domain.open_stream(timeout=1))
    assert entered.wait(0.5)
    second = Call(lambda: domain.open_stream(timeout=0.1))
    try:
        with pytest.raises(WatcheRobotError):
            second.result(0.25)
    finally:
        release.set()
    owner = first.result()
    assert len(robot.commands) == 1
    owner.close()


def test_rejected_open_does_not_close_another_device_stream_and_allows_retry():
    domain, robot = make_domain()

    def reject(kind, data, timeout):
        raise CommandError(kind, "busy")
    robot.on_command = reject
    with pytest.raises(CommandError, match="busy"):
        domain.open_stream()
    assert [kind for kind, _, _ in robot.commands] == ["ctrl.display.open"]
    robot.on_command = None
    domain.open_stream().close()


def test_open_timeout_has_no_guessed_stream_cleanup():
    domain, robot = make_domain()
    robot.on_command = lambda *args: (_ for _ in ()).throw(TimeoutError("open timeout"))
    with pytest.raises(TimeoutError):
        domain.open_stream(timeout=0.02)
    domain._close()
    assert [kind for kind, _, _ in robot.commands] == ["ctrl.display.open"]


def test_disconnect_during_open_never_installs_or_closes_old_connection_session():
    domain, robot = make_domain()
    robot.on_command = lambda *args: domain._disconnected()
    with pytest.raises(WatcheRobotError, match="disconnect"):
        domain.open_stream()
    assert len(robot.commands) == 1
    robot.on_command = None
    domain.open_stream().close()


def test_close_idempotent_and_domain_shutdown_prevents_further_opens():
    domain, robot = make_domain()
    session = domain.open_stream()
    domain._close()
    session.close()
    domain._close()
    assert [kind for kind, _, _ in robot.commands] == [
        "ctrl.display.open", "ctrl.display.close",
    ]
    with pytest.raises(WatcheRobotError):
        domain.open_stream()


def test_close_timeout_preserves_ownership_for_explicit_retry():
    domain, robot = make_domain()
    session = domain.open_stream()

    def close_timeout(kind, data, timeout):
        if kind == "ctrl.display.close":
            raise TimeoutError("close timeout")
    robot.on_command = close_timeout
    with pytest.raises(TimeoutError):
        session.close(timeout=0.02)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    with pytest.raises(WatcheRobotError):
        domain.open_stream()
    robot.on_command = None
    session.close(timeout=0.1)
    domain.open_stream().close()
    assert all(data == {"stream_id": 41} for kind, data, _ in robot.commands
               if kind == "ctrl.display.close")


def test_concurrent_close_sends_one_command():
    domain, robot = make_domain()
    session = domain.open_stream()
    entered, release = threading.Event(), threading.Event()

    def hold_close(kind, data, timeout):
        if kind == "ctrl.display.close":
            entered.set()
            assert release.wait(0.8)
    robot.on_command = hold_close
    first = Call(lambda: session.close(timeout=1))
    assert entered.wait(0.5)
    second = Call(lambda: session.close(timeout=0.05))
    try:
        with pytest.raises(TimeoutError):
            second.result(0.25)
    finally:
        release.set()
    first.result()
    session.close()
    assert [kind for kind, _, _ in robot.commands].count("ctrl.display.close") == 1


def test_context_body_error_still_closes_owned_stream():
    domain, robot = make_domain()
    with pytest.raises(LookupError, match="body"):
        with domain.open_stream():
            raise LookupError("body")
    assert robot.commands[-1][:2] == ("ctrl.display.close", {"stream_id": 41})



def test_bare_ack_data_is_not_an_ack_envelope():
    domain, robot = make_domain()
    robot.open_ack = {"stream_id": 41, "max_frame_bytes": 65536}
    with pytest.raises(WatcheRobotError):
        domain.open_stream()
    assert robot._transport.packets == []


def test_shutdown_waits_for_reserved_open_then_closes_its_owned_stream():
    domain, robot = make_domain()
    entered, release = threading.Event(), threading.Event()

    def hold_open(kind, data, timeout):
        if kind == "ctrl.display.open":
            entered.set()
            assert release.wait(0.8)
    robot.on_command = hold_open
    opening = Call(lambda: domain.open_stream(timeout=1))
    assert entered.wait(0.5)
    closing = Call(domain._close)
    assert not closing.done.wait(0.02)
    release.set()
    with pytest.raises(WatcheRobotError):
        opening.result()
    closing.result()
    assert [kind for kind, _, _ in robot.commands] == [
        "ctrl.display.open", "ctrl.display.close",
    ]


def test_send_device_raising_synchronously_disables_session():
    domain, robot = make_domain()
    session = domain.open_stream()

    def fail_send(_):
        raise OSError("send failed")
    robot._transport.on_send = fail_send
    with pytest.raises(OSError, match="send failed"):
        session.present(JPEG)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    assert len(robot._transport.packets) == 1
    session.close()


@pytest.mark.parametrize("timeout", [121, 10 ** 1000])
def test_out_of_range_timeout_is_rejected_consistently_before_robot_calls(timeout):
    domain, robot = make_domain()
    with pytest.raises(ValueError):
        domain.open_stream(timeout=timeout)
    assert robot.commands == []
    assert robot.capabilities == []


def test_unowned_session_handle_cannot_send_or_close_another_stream():
    domain, robot = make_domain()
    display = importlib.import_module("watcherobot.display")
    with domain.open_stream() as owner:
        unowned = display.DisplaySession(domain, 42, 65536, None)
        with pytest.raises(WatcheRobotError):
            unowned.present(JPEG, timeout=0.02)
        unowned.close()
        assert robot._transport.packets == []
        assert len(robot.commands) == 1
        auto_ack(domain, robot)
        assert owner.present(JPEG) == 1


@pytest.mark.parametrize("size", [206, 412])
def test_jpeg_dimensions_are_independent_of_logical_canvas(size):
    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (size, size), (80, 120, 160)).save(buffer, format="JPEG")
    jpeg = buffer.getvalue()
    domain, robot = make_domain()
    auto_ack(domain, robot)
    with domain.open_stream() as session:
        assert session.present(jpeg) == 1
    assert robot._transport.packets[0][16:] == jpeg
    assert robot.commands[0][1] == {"width": 412, "height": 412, "format": "jpeg"}


def test_close_rejection_keeps_owner_unusable_and_cleanup_retryable():
    domain, robot = make_domain()
    session = domain.open_stream()

    def reject_close(kind, data, timeout):
        if kind == "ctrl.display.close":
            raise CommandError(kind, "busy")
    robot.on_command = reject_close
    with pytest.raises(CommandError, match="busy"):
        session.close(timeout=0.1)
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    with pytest.raises(WatcheRobotError):
        domain.open_stream()
    robot.on_command = None
    session.close()


def test_late_open_ack_keeps_only_cleanup_ownership(monkeypatch):
    domain, robot = make_domain()
    display = importlib.import_module("watcherobot.display")
    now = [100.0]
    monkeypatch.setattr(display.time, "monotonic", lambda: now[0])

    def late_ack(kind, data, timeout):
        now[0] += 2
    robot.on_command = late_ack
    with pytest.raises(TimeoutError):
        domain.open_stream(timeout=1)
    with pytest.raises(WatcheRobotError):
        domain.open_stream()
    robot.on_command = None
    domain._close()
    assert robot.commands[-1][:2] == ("ctrl.display.close", {"stream_id": 41})


@pytest.mark.parametrize("fail_first_close", [False, True])
def test_late_open_public_cleanup_allows_retry_and_reopen(monkeypatch, fail_first_close):
    domain, robot = make_domain()
    display = importlib.import_module("watcherobot.display")
    now = [100.0]
    monkeypatch.setattr(display.time, "monotonic", lambda: now[0])

    def late_ack(kind, data, timeout):
        if kind == "ctrl.display.open":
            now[0] += 2

    robot.on_command = late_ack
    with pytest.raises(TimeoutError, match="timed out"):
        domain.open_stream(timeout=1)

    if fail_first_close:
        def reject_close(kind, data, timeout):
            raise CommandError(kind, "busy")
        robot.on_command = reject_close
        with pytest.raises(CommandError, match="busy"):
            domain.close_stream(timeout=0.1)
        with pytest.raises(WatcheRobotError, match="already open"):
            domain.open_stream()

    robot.on_command = None
    domain.close_stream(timeout=0.1)
    assert robot.commands[-1][:2] == ("ctrl.display.close", {"stream_id": 41})
    with domain.open_stream() as reopened:
        auto_ack(domain, robot)
        assert reopened.present(JPEG) == 1


def test_public_cleanup_without_confirmed_ownership_sends_no_command():
    domain, robot = make_domain()
    domain.close_stream()
    assert robot.commands == []


def test_public_cleanup_waits_for_open_and_times_out_without_losing_ownership():
    domain, robot = make_domain()
    entered = threading.Event()
    release = threading.Event()

    def hold_open(kind, data, timeout):
        if kind == "ctrl.display.open":
            entered.set()
            assert release.wait(1)

    robot.on_command = hold_open
    opening = Call(lambda: domain.open_stream(timeout=1))
    assert entered.wait(1)
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            domain.close_stream(timeout=0.02)
    finally:
        release.set()
    session = opening.result()
    domain.close_stream()
    with pytest.raises(WatcheRobotError):
        session.present(JPEG)
    assert robot.commands[-1][:2] == ("ctrl.display.close", {"stream_id": 41})
