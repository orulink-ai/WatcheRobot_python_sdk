"""Versioned, immutable input/output contracts. No SDK, UI or provider imports."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

REQUEST_SCHEMA = "watche.behavior.request.v1"
PLAN_SCHEMA = "watche.behavior.plan.v1"
INTENTS = frozenset(("greeting", "affirm", "deny", "question", "emphasis", "neutral"))


def _keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"Invalid {label} fields")


def _number(value: Any, low: float, high: float, label: str, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if integer and not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{label} is outside its supported range")
    return value


@dataclass(frozen=True)
class AudioEnvelope:
    duration_ms: float
    levels: tuple[int, ...]
    step_ms: int = 20

    def level_at(self, time_ms: float) -> int:
        if not 0 <= time_ms < self.duration_ms:
            return 0
        return self.levels[min(len(self.levels) - 1, int(time_ms // self.step_ms))]


@dataclass(frozen=True)
class TimedSegment:
    text: str
    start_ms: float
    end_ms: float
    intent: str | None = None
    emotion: int | None = None


@dataclass(frozen=True)
class BehaviorRequest:
    audio: AudioEnvelope
    transcript: str = ""
    emotion_mode: str = "auto"
    emotion_target: int = 0
    strength: float = .85
    segments: tuple[TimedSegment, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviorRequest:
        _keys(data, {"schema", "audio", "transcript", "emotionMode", "emotionTarget", "strength", "segments"}, "request")
        if data.get("schema") != REQUEST_SCHEMA:
            raise ValueError("Unsupported behavior request schema")
        audio = data.get("audio", {})
        _keys(audio, {"durationMs", "levels", "stepMs"}, "audio")
        duration = _number(audio.get("durationMs"), .001, 60000, "durationMs")
        if audio.get("stepMs", 20) != 20 or isinstance(audio.get("stepMs"), bool):
            raise ValueError("Audio envelope stepMs must be 20")
        levels = audio.get("levels")
        if not isinstance(levels, list) or len(levels) != math.ceil(duration / 20):
            raise ValueError("Audio levels must cover the entire duration in 20ms steps")
        validated_levels = tuple(int(_number(v, 0, 1000, "level", integer=True)) for v in levels)
        transcript = data.get("transcript", "")
        if not isinstance(transcript, str) or len(transcript) > 2000:
            raise ValueError("Transcript must contain at most 2000 characters")
        mode = data.get("emotionMode", "auto")
        if mode not in ("auto", "manual"):
            raise ValueError("emotionMode must be auto or manual")
        emotion = int(_number(data.get("emotionTarget", 0), 0, 6, "emotionTarget", integer=True))
        strength = _number(data.get("strength", .85), 0, 1.2, "strength")
        raw_segments = data.get("segments", [])
        if not isinstance(raw_segments, list) or len(raw_segments) > 128:
            raise ValueError("At most 128 timed segments are supported")
        segments = []
        previous_end = 0.0
        characters = 0
        for item in raw_segments:
            _keys(item, {"text", "startMs", "endMs", "intent", "emotion"}, "segment")
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Timed segments require non-empty text")
            characters += len(text)
            if characters > 2000:
                raise ValueError("Timed segment text exceeds 2000 characters")
            start = _number(item.get("startMs"), previous_end, duration, "segment startMs")
            end = _number(item.get("endMs"), start, duration, "segment endMs")
            if end <= start:
                raise ValueError("Timed segments must have positive duration")
            intent = item.get("intent")
            if intent is not None and (not isinstance(intent, str) or intent not in INTENTS):
                raise ValueError("Unknown segment intent")
            hint = item.get("emotion")
            if hint is not None:
                hint = int(_number(hint, 0, 6, "segment emotion", integer=True))
            segments.append(TimedSegment(text, start, end, intent, hint))
            previous_end = end
        return cls(AudioEnvelope(duration, validated_levels), transcript, mode, emotion, strength, tuple(segments))


@dataclass(frozen=True)
class BehaviorCue:
    text: str
    intent: str
    emotion: int
    start_ms: float
    end_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "intent": self.intent, "emotion": self.emotion,
                "startMs": self.start_ms, "endMs": self.end_ms}


@dataclass(frozen=True)
class BehaviorFrame:
    time_ms: float
    pan_deg: float
    tilt_deg: float
    mouth_level: int
    emotion_target: int
    speaking: bool

    def to_dict(self) -> dict[str, Any]:
        return {"timeMs": self.time_ms, "panDeg": self.pan_deg, "tiltDeg": self.tilt_deg,
                "mouthLevel": self.mouth_level, "emotionTarget": self.emotion_target, "speaking": self.speaking}
