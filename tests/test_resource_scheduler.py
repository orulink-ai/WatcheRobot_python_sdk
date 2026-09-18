from __future__ import annotations

import threading

import pytest

from watcherobot.resource_scheduler import (
    FifoResourceScheduler,
    ResourceQueueClosedError,
)


def test_scheduler_grants_waiters_in_fifo_order() -> None:
    scheduler = FifoResourceScheduler()
    first = scheduler.request()
    second = scheduler.request()
    third = scheduler.request()

    assert first.granted
    assert second.state == "queued"
    assert third.state == "queued"

    first.release()
    assert second.wait(0) is second
    assert third.state == "queued"

    second.release()
    assert third.wait(0) is third


def test_scheduler_cancels_a_waiter_without_disturbing_the_owner() -> None:
    scheduler = FifoResourceScheduler()
    owner = scheduler.request()
    waiting = scheduler.request()

    assert waiting.cancel() is True
    assert waiting.state == "cancelled"
    assert owner.granted


def test_scheduler_timeout_removes_waiter() -> None:
    scheduler = FifoResourceScheduler()
    owner = scheduler.request()
    waiting = scheduler.request()

    with pytest.raises(TimeoutError, match="did not start"):
        waiting.wait(0.01)

    owner.release()
    replacement = scheduler.request()
    assert replacement.granted


def test_scheduler_close_wakes_all_waiters() -> None:
    scheduler = FifoResourceScheduler()
    scheduler.request()
    waiting = scheduler.request()
    started = threading.Event()
    errors: list[Exception] = []

    def wait_for_resource() -> None:
        started.set()
        try:
            waiting.wait(1.0)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=wait_for_resource)
    thread.start()
    assert started.wait(1.0)

    scheduler.close("disconnected")
    thread.join(1.0)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ResourceQueueClosedError)
    assert errors[0].reason == "disconnected"
