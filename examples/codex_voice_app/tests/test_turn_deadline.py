from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
from turn_deadline import TurnDeadline


def test_completed_task_releases_its_clock_before_voice_connects_and_plays():
    deadline = TurnDeadline(0)
    deadline.begin_voice(89)
    deadline.voice_requested(100)
    assert not deadline.error(130)  # Old 90-second clock must not interrupt speech.
    deadline.audio_progress(131)
    deadline.playback_started(132)
    assert not deadline.error(175)
    deadline.playback_finished(176)
    deadline.finish()
    assert not deadline.error(10000)


def test_task_progress_extends_only_idle_deadline_not_absolute_budget():
    deadline = TurnDeadline(0)
    deadline.task_progress(80)
    assert not deadline.error(95)
    assert '进度' in deadline.error(171)
    deadline.task_progress(299)
    assert '5分钟' in deadline.error(301)


def test_voice_connection_first_audio_and_streaming_have_distinct_deadlines():
    deadline = TurnDeadline(0)
    deadline.begin_voice(20)
    assert not deadline.error(79)
    assert '连接' in deadline.error(81)
    deadline.voice_requested(82)
    assert not deadline.error(126)
    assert '语音' in deadline.error(128)
    deadline.audio_progress(129)
    assert not deadline.error(158)
    assert '中断' in deadline.error(160)


def test_playback_uses_sdk_completion_bound_and_still_has_absolute_voice_budget():
    deadline = TurnDeadline(0)
    deadline.begin_voice(10)
    deadline.voice_requested(11)
    deadline.audio_progress(12)
    deadline.playback_started(13)
    assert not deadline.error(100)
    assert '3分钟' in deadline.error(191)


def test_fast_audio_cannot_be_reset_to_waiting_and_terminal_deadline_cannot_revive():
    deadline = TurnDeadline(0)
    deadline.begin_voice(1)
    deadline.audio_progress(2)
    deadline.voice_requested(3)
    assert deadline.phase == 'streaming'
    deadline.finish()
    deadline.task_progress(4)
    deadline.audio_progress(5)
    deadline.playback_started(6)
    deadline.playback_finished(7)
    assert deadline.phase == 'done'
    assert not deadline.error(10000)
