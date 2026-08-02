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
from .inputs import BackTouchEvent, InputDomain, InputEvent, RollerEvent, ScreenTouchEvent
from .media import AudioFormat, AudioFrame, AudioRecording, ImageFrame, MicrophoneSession
from .vision import (
    FaceBox,
    FaceTrackingFrame,
    FaceTrackingPreview,
    FaceTrackingStopPolicy,
    FaceTrackingTelemetry,
)

__all__ = [
    "AudioFormat",
    "AudioFrame",
    "AudioPlayback",
    "AudioRecording",
    "AuthenticationError",
    "BackTouchEvent",
    "CommandError",
    "ConnectionTimeoutError",
    "FaceBox",
    "FaceTrackingFrame",
    "FaceTrackingPreview",
    "FaceTrackingStopPolicy",
    "FaceTrackingTelemetry",
    "ImageFrame",
    "InputDomain",
    "InputEvent",
    "Job",
    "JobCancelledError",
    "JobFailedError",
    "JobState",
    "MicrophoneSession",
    "PCMAudio",
    "RollerEvent",
    "ScreenTouchEvent",
    "WatcheRobotError",
]

__version__ = "0.1.0a4"
