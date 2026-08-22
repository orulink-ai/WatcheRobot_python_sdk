"""机器人闹钟示例的内部包：闹钟存储、调度与网页服务。"""

from reminder.alarms import AlarmStore, AlarmValidationError
from reminder.schedule import Alarm, next_fire_time, next_fires, seconds_until
from reminder.speech import ReminderSpeech, synthesize_text_to_wav
from reminder.web import AlarmWebServer

__all__ = [
    "Alarm",
    "AlarmStore",
    "AlarmValidationError",
    "AlarmWebServer",
    "ReminderSpeech",
    "next_fire_time",
    "next_fires",
    "seconds_until",
    "synthesize_text_to_wav",
]