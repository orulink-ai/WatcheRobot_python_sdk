"""Bounded mono 48k PCM reservoir. No VAD, sample dropping or time stretching."""
from collections import deque
import math


class PlayoutBuffer:
    BYTES_PER_MS = 96

    def __init__(self, clock, on_failure=None):
        self.clock, self.on_failure = clock, on_failure
        self.buffer = bytearray()
        self.arrivals = deque()
        self.prefill_ms, self.max_buffer_ms = 0, 2000
        self.age_limit_enabled = False
        self.waiting = self.failed = False
        self.prefill_started = None
        self.prefill_cycles = self.held_frames = 0
        self.high_water_ms = self.residence_ms = self.residence_max_ms = 0.0
        self.discarded_on_failure_ms = 0.0

    def configure(self, *, prefill_ms, max_buffer_ms):
        if (type(prefill_ms) is not int or type(max_buffer_ms) is not int
                or not 0 <= prefill_ms < max_buffer_ms <= 2000):
            raise ValueError('Invalid bounded playout configuration')
        if self.buffer or self.failed:
            raise RuntimeError('Configure playout before queuing audio')
        self.prefill_ms, self.max_buffer_ms = prefill_ms, max_buffer_ms
        self.age_limit_enabled = True

    def validate_growth(self, size):
        if self.failed:
            raise RuntimeError('Device RTC playout already failed')
        if len(self.buffer) + size > self.max_buffer_ms * self.BYTES_PER_MS:
            message = f'Device RTC audio backlog exceeds {self.max_buffer_ms}ms'
            if self.age_limit_enabled:
                self.fail(message)
            raise RuntimeError(message)

    def fail(self, message):
        if not self.failed:
            self.failed = True
            self.discarded_on_failure_ms += len(self.buffer) / self.BYTES_PER_MS
            self.clear()  # Fence PCM before scheduling the physical teardown.
            if self.on_failure:
                self.on_failure(message)
        raise RuntimeError(message)

    def validate_age(self, received_at):
        if self.failed:
            raise RuntimeError('Device RTC playout already failed')
        if received_at is None:
            return
        if type(received_at) not in (int, float) or not math.isfinite(received_at):
            raise ValueError('Invalid playout arrival timestamp')
        if (self.age_limit_enabled
                and (self.clock() - received_at) * 1000 > self.max_buffer_ms):
            self.fail('Device RTC playout is stale; stop and restart the conversation')

    def append(self, pcm, *, received_at=None):
        self.validate_age(received_at)
        self.validate_growth(len(pcm))
        if not self.buffer:
            self.arrivals.clear()
            self.prefill_started = self.clock()
            self.waiting = bool(self.prefill_ms)
            self.prefill_cycles += self.waiting
        self.buffer.extend(pcm)
        # Include event/DSP residence in the same age budget. Do not re-age
        # delayed audio merely because it just entered the final PCM FIFO.
        self.arrivals.append([len(pcm), self.clock() if received_at is None else received_at])
        self.high_water_ms = max(self.high_water_ms, len(self.buffer) / self.BYTES_PER_MS)

    def take(self, size):
        if self.failed:
            raise RuntimeError('Device RTC playout already failed')
        if not self.buffer:
            self.clear()
            return b''
        now = self.clock()
        age_ms = max(0, (now - self.arrivals[0][1]) * 1000)
        if self.age_limit_enabled and age_ms > self.max_buffer_ms:
            self.fail('Device RTC playout is stale; stop and restart the conversation')
        if self.waiting:
            # Release tiny final tails on a deadline, even if the watermark is
            # never reached. Count held ticks separately from ordinary underruns.
            if (len(self.buffer) < self.prefill_ms * self.BYTES_PER_MS
                    and (now - self.prefill_started) * 1000 < self.prefill_ms - .001):
                self.held_frames += 1
                return b''
            self.waiting = False
        self.residence_ms = age_ms
        self.residence_max_ms = max(self.residence_max_ms, age_ms)
        pcm = bytes(self.buffer[:size])
        del self.buffer[:size]
        remaining = len(pcm)
        while remaining and self.arrivals:
            count = min(remaining, self.arrivals[0][0])
            remaining -= count
            self.arrivals[0][0] -= count
            if not self.arrivals[0][0]:
                self.arrivals.popleft()
        if not self.buffer:
            self.clear()
        return pcm

    def clear(self):
        self.buffer.clear()
        self.arrivals.clear()
        self.waiting = False
        self.prefill_started = None

    def diagnostics(self):
        return dict(prefillMs=self.prefill_ms, maxBufferMs=self.max_buffer_ms,
                    bufferedMs=round(len(self.buffer) / self.BYTES_PER_MS, 3),
                    bufferHighWaterMs=round(self.high_water_ms, 3),
                    queueResidenceMs=round(self.residence_ms, 3),
                    queueResidenceMaxMs=round(self.residence_max_ms, 3),
                    oldestQueuedMs=round(max(0, (self.clock() - self.arrivals[0][1]) * 1000), 3)
                    if self.arrivals and self.buffer else 0,
                    waiting=self.waiting, prefillCycles=self.prefill_cycles,
                    heldFrames=self.held_frames, failed=self.failed,
                    discardedOnFailureMs=round(self.discarded_on_failure_ms, 3))
