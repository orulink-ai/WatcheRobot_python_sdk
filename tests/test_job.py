import threading

import pytest

from watcherobot import JobCancelledError, JobFailedError, JobState
from watcherobot.job import Job


class FakeTransport:
    def __init__(self):
        self.commands = []

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, data, timeout))
        return {"type": "sys.ack", "code": 0, "data": {"command_id": "cancel"}}


def test_job_wait_follows_operation_lifecycle():
    job = Job(42, FakeTransport(), initial_state=JobState.STARTING)

    threading.Timer(0.01, lambda: job._update(JobState.RUNNING)).start()
    threading.Timer(0.02, lambda: job._update(JobState.COMPLETED)).start()

    assert job.wait(timeout=1) is job
    assert job.state is JobState.COMPLETED


def test_job_wait_reports_failure_and_cancel():
    failed = Job(3, FakeTransport())
    failed._update(JobState.FAILED, error_code=17)
    with pytest.raises(JobFailedError, match="17"):
        failed.wait(timeout=0)

    cancelled = Job(4, FakeTransport())
    cancelled._update(JobState.CANCELLED)
    with pytest.raises(JobCancelledError):
        cancelled.wait(timeout=0)


def test_job_cancel_is_idempotent_after_terminal_state():
    transport = FakeTransport()
    job = Job(11, transport)

    job.cancel()
    job._update(JobState.CANCELLED)
    job.cancel()

    assert transport.commands == [("ctrl.job.cancel", {"operation_id": 11}, None)]

