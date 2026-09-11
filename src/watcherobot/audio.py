from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .job import CommandTransport, Job, JobState
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
        transport: CommandTransport,
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
    """Read a WAV file already encoded in the protocol-v1 playback format."""
    source = Path(path)
    try:
        with wave.open(str(source), "rb") as wav_file:
            if wav_file.getframerate() != OUTPUT_AUDIO_FORMAT.sample_rate_hz:
                raise ValueError("v1 playback WAV must use 24000 Hz")
            if wav_file.getnchannels() != OUTPUT_AUDIO_FORMAT.channels:
                raise ValueError("v1 playback WAV must be mono")
            if wav_file.getsampwidth() != OUTPUT_AUDIO_FORMAT.sample_width_bytes:
                raise ValueError("v1 playback WAV must use 16-bit samples")
            if wav_file.getcomptype() != "NONE":
                raise ValueError("v1 playback requires an uncompressed PCM WAV file")
            return PCMAudio(wav_file.readframes(wav_file.getnframes()))
    except wave.Error as error:
        raise ValueError(f"invalid WAV file: {source}") from error


def load_audio_file(path: str | Path) -> PCMAudio:
    """Decode WAV, MP3, or OGG and resample to protocol-v1 playback PCM."""
    source = Path(path)
    if source.suffix.lower() not in {".wav", ".mp3", ".ogg"}:
        raise ValueError("audio file must be WAV, MP3, or OGG")
    try:
        import av

        chunks: list[bytes] = []
        with av.open(str(source)) as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                raise ValueError(f"audio file has no audio stream: {source}")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
            for frame in container.decode(streams[0]):
                if not isinstance(frame, av.AudioFrame):
                    continue
                converted = resampler.resample(frame)
                for output in converted:
                    chunks.append(bytes(output.planes[0])[: output.samples * 2])
            for output in resampler.resample(None):
                chunks.append(bytes(output.planes[0])[: output.samples * 2])
        return PCMAudio(b"".join(chunks))
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith(("audio file", "PCM audio")):
            raise
        raise ValueError(f"invalid or unsupported audio file: {source}") from error
