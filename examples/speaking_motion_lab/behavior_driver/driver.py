"""One reusable compiler/sampler for preview and future device execution adapters."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import PLAN_SCHEMA, BehaviorCue, BehaviorFrame, BehaviorRequest
from .planner import MotionProfile, align_cues, head_offsets, rhythm


@dataclass(frozen=True)
class BehaviorPlan:
    request: BehaviorRequest
    cues: tuple[BehaviorCue, ...]
    alignment: str
    phrases: tuple[tuple[float, float], ...]
    accents: tuple[float, ...]
    frames: tuple[BehaviorFrame, ...] = ()

    def to_dict(self) -> dict:
        return {"schema": PLAN_SCHEMA, "audioDurationMs": self.request.audio.duration_ms,
                "durationMs": self.frames[-1].time_ms, "alignment": self.alignment,
                "cues": [c.to_dict() for c in self.cues], "frames": [f.to_dict() for f in self.frames]}


class BehaviorDriver:
    """No clock or robot ownership: callers supply media time and choose an output adapter."""

    def __init__(self, profile: MotionProfile | None = None) -> None:
        self.profile = profile or MotionProfile()

    def plan(self, request: BehaviorRequest) -> BehaviorPlan:
        cues, alignment = align_cues(request)
        phrases, accents = rhythm(request.audio)
        plan = BehaviorPlan(request, cues, alignment, phrases, accents)
        duration = request.audio.duration_ms + self.profile.settle_ms
        frames = tuple(self.sample(plan, min(duration, i * self.profile.frame_ms))
                       for i in range(math.ceil(duration / self.profile.frame_ms) + 1))
        return BehaviorPlan(request, cues, alignment, phrases, accents, frames)

    def sample(self, plan: BehaviorPlan, time_ms: float) -> BehaviorFrame:
        if not math.isfinite(time_ms):
            raise ValueError("Media time must be finite")
        time = max(0, time_ms)
        request, profile = plan.request, self.profile
        speaking = time < request.audio.duration_ms
        cue = next((c for c in plan.cues if c.start_ms <= time < c.end_ms), None)
        emotion = 0
        if speaking:
            emotion = cue.emotion if cue else (request.emotion_target if request.emotion_mode == "manual" else 0)
        yaw, pitch = (0.0, 0.0)
        if 0 < time < request.audio.duration_ms:
            yaw, pitch = head_offsets(plan.cues, request.audio, time, plan.phrases, plan.accents)
        return BehaviorFrame(
            time,
            min(profile.pan_max, max(profile.pan_min, profile.neutral_pan + yaw * request.strength)),
            min(profile.tilt_max, max(profile.tilt_min, profile.neutral_tilt + pitch * request.strength)),
            min(1000, max(0, int(request.audio.level_at(time) * profile.mouth_gain + .5))),
            emotion, speaking,
        )
