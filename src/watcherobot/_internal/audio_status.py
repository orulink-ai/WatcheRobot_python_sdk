"""Single source of truth for device audio stream status semantics."""

from __future__ import annotations

from enum import Enum


class AudioStatusKind(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FAILED_AUDIO_REASONS = frozenset(
    {
        "enqueue_timeout",
        "enqueue_failed",
        "queue_lock_timeout",
        "sequence_gap",
        "stale_stream",
        "stale_frame",
        "playback_write_failed",
    }
)

CANCELLED_AUDIO_REASONS = frozenset({"aborted", "replaced_by_new_stream"})


def classify_audio_status(reason: object) -> AudioStatusKind:
    """Map one protocol reason to the public playback lifecycle."""

    if reason == "complete":
        return AudioStatusKind.COMPLETED
    if reason in FAILED_AUDIO_REASONS:
        return AudioStatusKind.FAILED
    if reason in CANCELLED_AUDIO_REASONS:
        return AudioStatusKind.CANCELLED
    return AudioStatusKind.RUNNING
