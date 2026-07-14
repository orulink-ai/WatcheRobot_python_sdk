from .errors import (
    AuthenticationError,
    CommandError,
    ConnectionTimeoutError,
    JobCancelledError,
    JobFailedError,
    WatcheRobotError,
)
from .audio import AudioPlayback, PCMAudio
from .job import Job, JobState
from .media import AudioFormat, AudioFrame, AudioRecording, ImageFrame, MicrophoneSession
from .robot import WatcheRobot

__all__ = [
    "AudioFormat",
    "AudioFrame",
    "AudioPlayback",
    "AudioRecording",
    "AuthenticationError",
    "CommandError",
    "ConnectionTimeoutError",
    "ImageFrame",
    "Job",
    "JobCancelledError",
    "JobFailedError",
    "JobState",
    "MicrophoneSession",
    "PCMAudio",
    "WatcheRobot",
    "WatcheRobotError",
]

__version__ = "0.1.0a1"
