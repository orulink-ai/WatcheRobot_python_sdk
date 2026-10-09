"""Time alignment, bounded motion profile and smooth trajectory composition."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import AudioEnvelope, BehaviorCue, BehaviorRequest
from .semantics import classify_clause, clauses


@dataclass(frozen=True)
class MotionProfile:
    neutral_pan: float = 90
    neutral_tilt: float = 108
    pan_min: float = 76
    pan_max: float = 104
    tilt_min: float = 100
    tilt_max: float = 122
    mouth_gain: float = 2.5
    frame_ms: int = 40
    settle_ms: int = 1200


def voiced_windows(audio: AudioEnvelope) -> tuple[tuple[float, float], ...]:
    windows: list[tuple[float, float]] = []
    for i, level in enumerate(audio.levels):
        if level <= 20:
            continue
        start, end = i * audio.step_ms, min((i + 1) * audio.step_ms, audio.duration_ms)
        if windows and windows[-1][1] == start:
            windows[-1] = (windows[-1][0], end)
        else:
            windows.append((start, end))
    return tuple(windows)


def align_cues(request: BehaviorRequest) -> tuple[tuple[BehaviorCue, ...], str]:
    def cue(text: str, start: float, end: float, intent: str | None = None, emotion: int | None = None) -> BehaviorCue:
        inferred_intent, inferred_emotion = classify_clause(text)
        target = request.emotion_target if request.emotion_mode == "manual" else (inferred_emotion if emotion is None else emotion)
        return BehaviorCue(text, intent or inferred_intent, target, start, end)
    if request.segments:
        return tuple(cue(s.text, s.start_ms, s.end_ms, s.intent, s.emotion) for s in request.segments), "provided"
    texts = clauses(request.transcript)
    windows = voiced_windows(request.audio)
    if not texts or not windows:
        return (), "no-text" if not texts else "estimated"
    weights = [sum(c.isalnum() for c in text) for text in texts]
    total = sum(weights)
    voice_duration = sum(end - start for start, end in windows)
    def at(offset: float, *, end_boundary: bool = False) -> float:
        for start, end in windows:
            length = end - start
            if offset < length or (offset == length and end_boundary):
                return start + offset
            offset -= length
        return windows[-1][1]
    used = 0
    result = []
    for text, weight in zip(texts, weights):
        start = at(used / total * voice_duration)
        used += weight
        result.append(cue(text, start, at(used / total * voice_duration, end_boundary=True)))
    return tuple(result), "estimated"


def smooth(t: float) -> float:
    t = min(1, max(0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def pulse(time: float, start: float, peak: float, end: float) -> float:
    if time <= start or time >= end:
        return 0
    return smooth((time - start) / (peak - start)) if time <= peak else 1 - smooth((time - peak) / (end - peak))


def rhythm(audio: AudioEnvelope) -> tuple[tuple[tuple[float, float], ...], tuple[float, ...]]:
    phrases: list[tuple[float, float]] = []
    start = last = -1.0
    for i, level in enumerate(audio.levels):
        time = i * audio.step_ms
        if level > 20:
            if start < 0:
                start = time
            last = time
        if start >= 0 and (time - last >= 300 or i == len(audio.levels) - 1):
            phrases.append((start, min(audio.duration_ms, last + audio.step_ms)))
            start = -1
    accents = []
    for beginning, end in phrases:
        cursor = beginning + 300
        while cursor < end - 100:
            candidates = range(int(cursor), int(min(cursor + 480, end - 100)), audio.step_ms)
            best = max(candidates, key=audio.level_at, default=cursor)
            if audio.level_at(best) > 50 and 400 < best < audio.duration_ms - 600:
                accents.append(best)
            cursor += 1100
    return tuple(phrases), tuple(accents)


MOODS = ((1, 0, 0), (1.05, -1, 0), (1.25, -2, 0), (.8, -3, 0), (.65, -1.5, 0), (.55, 5, 0), (.5, 2, 3))


def head_offsets(cues: tuple[BehaviorCue, ...], audio: AudioEnvelope, time: float,
                 phrases: tuple[tuple[float, float], ...], accents: tuple[float, ...]) -> tuple[float, float]:
    yaw = sum((-1 if i % 2 else 1) * 5 * pulse(time, max(0, start - 100), (start + end) / 2,
              min(audio.duration_ms, end + 350)) for i, (start, end) in enumerate(phrases))
    pitch = sum(5 * pulse(time, t - 220, t + 70, t + 580) for t in accents)
    active = next((c for c in cues if c.start_ms <= time < c.end_ms), None)
    if active is None:
        return (0, 0) if cues else (yaw, pitch)
    duration = active.end_ms - active.start_ms
    p = (time - active.start_ms) / duration
    e = smooth(p * 2 if p < .5 else (1 - p) * 2) * min(1, duration / 1600)
    if active.intent == "affirm":
        yaw, pitch = 0, 7 * e
    elif active.intent == "deny":
        yaw, pitch = 9 * e * math.sin(p * math.pi * 4), 0
    elif active.intent == "question":
        yaw, pitch = 5 * e, -3.5 * e
    elif active.intent == "greeting":
        yaw, pitch = 0, 4 * e
    elif active.intent == "emphasis":
        yaw, pitch = 0, 6 * e
    else:
        yaw, pitch = yaw * e, pitch * e
    gain, tilt, pan = MOODS[active.emotion]
    return yaw * gain + pan * e, pitch * gain + tilt * e
