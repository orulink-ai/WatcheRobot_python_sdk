"""Pure phase-aware deadlines. Transport heartbeats are not task progress."""
TASK_IDLE_SECONDS = 90
TASK_MAX_SECONDS = 300
VOICE_CONNECT_SECONDS = 60
VOICE_FIRST_AUDIO_SECONDS = 45
VOICE_IDLE_SECONDS = 30
VOICE_MAX_SECONDS = 180


class TurnDeadline:
    def __init__(self, now: float):
        self.phase = 'task'
        self.started = self.phase_started = self.last_progress = now
        self.voice_started = None

    def task_progress(self, now):
        if self.phase == 'task':
            self.last_progress = now

    def begin_voice(self, now):
        if self.phase == 'task':
            self.phase = 'connecting'
            self.voice_started = self.phase_started = self.last_progress = now

    def voice_requested(self, now):
        if self.phase == 'connecting':
            self.phase = 'awaiting-audio'
            self.phase_started = self.last_progress = now

    def audio_progress(self, now):
        if self.phase in {'connecting', 'awaiting-audio', 'streaming', 'playing'}:
            if self.phase != 'playing':
                self.phase = 'streaming'
            self.last_progress = now

    def playback_started(self, now):
        if self.phase in {'connecting', 'awaiting-audio', 'streaming'}:
            self.phase = 'playing'
            self.last_progress = now

    def playback_finished(self, now):
        if self.phase == 'playing':
            self.phase = 'streaming'
            self.last_progress = now

    def finish(self):
        self.phase = 'done'

    def can_finish(self, now):
        return (self.phase in {'connecting', 'awaiting-audio', 'streaming', 'playing'}
                and self.voice_started is not None and now - self.voice_started < VOICE_MAX_SECONDS)

    def error(self, now, *, progress_pending=False) -> str:
        if self.phase == 'done':
            return ''
        if self.phase == 'task':
            if now - self.started >= TASK_MAX_SECONDS:
                return 'Codex任务达到5分钟安全上限，正在结束会话'
            if progress_pending:
                return ''  # Dispatch queued events before judging old idle state.
            if now - self.last_progress >= TASK_IDLE_SECONDS:
                return 'Codex任务90秒没有新的进度，正在结束会话'
            return ''
        if self.voice_started is not None and now - self.voice_started >= VOICE_MAX_SECONDS:
            return '本轮语音达到3分钟安全上限，正在结束会话'
        if progress_pending:
            return ''  # No renewal of timestamps or absolute budgets.
        if self.phase == 'connecting' and now - self.phase_started >= VOICE_CONNECT_SECONDS:
            return '文字回答已生成，但语音连接超过60秒，正在结束会话'
        if self.phase == 'awaiting-audio' and now - self.phase_started >= VOICE_FIRST_AUDIO_SECONDS:
            return '文字回答已生成，但45秒内未收到语音，正在结束会话'
        if self.phase == 'streaming' and now - self.last_progress >= VOICE_IDLE_SECONDS:
            return '语音输出中断30秒且未确认播完，正在结束会话'
        # The playing phase is bounded by the SDK job wait (duration + 10 s),
        # not an idle clock; buffered audio is legitimate playback progress.
        return ''
