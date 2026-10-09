"""Local high-resolution media clock; never modifies the global asyncio clock."""
import asyncio
import time
import sys
from collections import deque


async def media_sleep(delay):
    if sys.platform == 'win32':
        # Python >=3.11 time.sleep uses a high-resolution Windows waitable
        # timer. Waiting in a worker avoids blocking the audio/control loop;
        # asyncio's coarse loop clock is never used for this deadline.
        await asyncio.to_thread(time.sleep, delay)
    else:
        await asyncio.sleep(delay)


class ArrivalCadence:
    """Arrival/PTS relative skew, not absolute latency or network-loss proof."""
    def __init__(self):
        self.last_at = self.anchor = None
        self.frames = self.long_intervals = 0
        self.interval = self.max_interval = 0.0
        self.skew = None
        self.last_pts = None

    def note(self, received_at, pts):
        if self.last_at is not None:
            self.interval = max(0, (received_at - self.last_at) * 1000)
            self.max_interval = max(self.max_interval, self.interval)
            self.long_intervals += self.interval > 40
        if pts is None:
            self.anchor = self.skew = None
        else:
            if self.anchor is None or self.last_pts is not None and pts < self.last_pts:
                self.anchor = received_at, pts
            self.skew = ((received_at - self.anchor[0]) - (pts - self.anchor[1])) * 1000
        self.last_at, self.last_pts = received_at, pts
        self.frames += 1

    def diagnostics(self):
        return dict(clock='perf-counter', frames=self.frames, intervalMs=round(self.interval, 3),
                    intervalMaxMs=round(self.max_interval, 3), intervalsOver40Ms=self.long_intervals,
                    arrivalMediaSkewMs=round(self.skew, 3) if self.skew is not None else None)


class MediaPacer:
    def __init__(self, *, clock=time.perf_counter, sleep=None):
        self.clock, self.sleep = clock, sleep or media_sleep
        self.last_at = None
        self.next_at = None
        self.frames = self.long_intervals = 0
        self.min_interval = self.max_interval = self.last_interval = 0.0
        self.intervals = deque(maxlen=512)

    async def wait(self):
        # Python 3.12 on Windows can wake asyncio timers early because its
        # loop clock is GetTickCount64. Verify against QPC after every wake.
        # Re-anchor after a stall: preserve all PCM/PTS without catch-up bursts.
        deadline = self.next_at
        if deadline is not None:
            while (remaining := deadline - self.clock()) > 0:
                await self.sleep(remaining)
            # Enforce minimum *actual* spacing after waking, not minimum wait
            # requested before waking. The latter compounds moderate late
            # wakeups forever (11ms lateness would slow 50Hz to about32Hz).
            while (remaining := self.last_at + .01 - self.clock()) > 0:
                await self.sleep(remaining)
        now = self.clock()
        if self.last_at is not None:
            interval = (now - self.last_at) * 1000
            self.last_interval = interval
            self.intervals.append(interval)
            self.min_interval = min(self.min_interval, interval) if self.frames > 1 else interval
            self.max_interval = max(self.max_interval, interval)
            self.long_intervals += interval > 40
        self.last_at = now
        # Keep the 50Hz timeline for ordinary wake-up jitter; continually
        # re-anchoring would slowly accumulate backlog on an infinite stream.
        # Recover at most two frames of ordinary scheduling lateness. The
        # minimum actual interval bounds that recovery; reset beyond 40ms.
        self.next_at = now + .02 if deadline is None or now - deadline >= .04 else deadline + .02
        self.frames += 1

    def diagnostics(self):
        recent = sorted(self.intervals)
        return dict(clock='perf-counter', frames=self.frames,
                    intervalWindowFrames=len(recent),
                    intervalP50Ms=round(recent[len(recent) // 2], 3) if recent else None,
                    intervalP95Ms=round(recent[min(len(recent) - 1, int(len(recent) * .95))], 3) if recent else None,
                    intervalMs=round(self.last_interval, 3),
                    intervalMinMs=round(self.min_interval, 3),
                    intervalMaxMs=round(self.max_interval, 3), intervalsOver40Ms=self.long_intervals)
