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


class JobFailedError(JobError):
    """A Job reached the failed state."""


class JobCancelledError(JobError):
    """A Job reached the cancelled state."""

