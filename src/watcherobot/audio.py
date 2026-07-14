from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .job import Job, JobState
from .media import AudioFormat

OUTPUT_AUDIO_FORMAT = AudioFormat(
    sample_rate_hz=24000,
    channels=1,
    sample_width_bytes=2,
    encoding="pcm_s16le",
)
MAX_AUDIO_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class PCMAudio:
    data: bytes
    audio_format: AudioFormat = OUTPUT_AUDIO_FORMAT

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("PCM audio must not be empty")
        if len(self.data) > MAX_AUDIO_BYTES:
            raise ValueError(f"PCM audio exceeds the {MAX_AUDIO_BYTES}-byte v1 limit")
        if len(self.data) % self.audio_format.sample_width_bytes != 0:
            raise ValueError("PCM audio ends with a partial sample")
        if self.audio_format != OUTPUT_AUDIO_FORMAT:
            raise ValueError("v1 playback requires PCM S16LE, 24000 Hz, mono")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class AudioPlayback(Job):
    """Job-compatible handle for one host-to-robot PCM stream."""

    def __init__(
        self,
        stream_id: int,
        transport,
        expected_sha256: str,
        cancel_callback: Callable[[AudioPlayback], None],
    ) -> None:
        super().__init__(stream_id, transport, initial_state=JobState.STARTING)
        self.expected_sha256 = expected_sha256
        self._cancel_callback = cancel_callback

    def cancel(self) -> None:
        if self.state.terminal:
            return
        self._cancel_callback(self)


def load_pcm_wave(path: str | Path) -> PCMAudio:
    """Read a WAV file in the single playback format supported by protocol v1."""
    source = Path(path)
    try:
        with wave.open(str(source), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            compression = wav_file.getcomptype()
            if compression != "NONE":
                raise ValueError("v1 playback requires an uncompressed PCM WAV file")
            if sample_rate != OUTPUT_AUDIO_FORMAT.sample_rate_hz:
                raise ValueError("v1 playback WAV must use 24000 Hz")
            if channels != OUTPUT_AUDIO_FORMAT.channels:
                raise ValueError("v1 playback WAV must be mono")
            if sample_width != OUTPUT_AUDIO_FORMAT.sample_width_bytes:
                raise ValueError("v1 playback WAV must use 16-bit samples")
            data = wav_file.readframes(wav_file.getnframes())
    except wave.Error as error:
        raise ValueError(f"invalid WAV file: {source}") from error
    return PCMAudio(data)
