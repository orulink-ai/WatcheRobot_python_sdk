class WatcheRobotError(Exception):
    """Base exception for the WatcheRobot SDK."""


class ConnectionTimeoutError(WatcheRobotError):
    """No SDK Control App connected before the timeout."""


class AuthenticationError(WatcheRobotError):
    """The temporary pairing code or protocol version was rejected."""


class CommandError(WatcheRobotError):
    """The robot rejected a command."""

    def __init__(self, message_type: str, reason: str):
        super().__init__(f"{message_type} rejected: {reason}")
        self.message_type = message_type
        self.reason = reason


class JobError(WatcheRobotError):
    """Base exception for a terminal Job error."""

    def __init__(
        self,
        job_id: int,
        *,
        reason: str | None = None,
        error_code: int | None = None,
        state: str,
    ) -> None:
        details = []
        if reason:
            details.append(f"reason={reason}")
        if error_code is not None:
            details.append(f"error_code={error_code}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(f"Job {job_id} {state}{suffix}")
        self.job_id = job_id
        self.reason = reason
        self.error_code = error_code


class JobFailedError(JobError):
    """A Job reached the failed state."""


class JobCancelledError(JobError):
    """A Job reached the cancelled state."""
