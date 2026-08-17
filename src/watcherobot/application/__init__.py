"""Public API for Daemon-managed WatcheRobot Applications."""

from .channels import (
    ApplicationChannel,
    ApplicationChannels,
    Frame,
    FrameCallback,
)
from .context import ApplicationContext, ApplicationEnvironmentError
from .rtc import (
    ApplicationRtc,
    RTC_AUDIO_CAPABILITY,
    RTC_PROTOCOL,
    RTC_VIDEO_CAPABILITY,
    RtcSessionRejectedError,
)

__all__ = [
    "ApplicationChannel",
    "ApplicationChannels",
    "ApplicationContext",
    "ApplicationEnvironmentError",
    "ApplicationRtc",
    "Frame",
    "FrameCallback",
    "RTC_AUDIO_CAPABILITY",
    "RTC_PROTOCOL",
    "RTC_VIDEO_CAPABILITY",
    "RtcSessionRejectedError",
]
