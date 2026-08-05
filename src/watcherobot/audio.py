from __future__ import annotations

import hashlib
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Callable

from .errors import WatcheRobotError
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


class AudioLiveStream:
    """Synchronous, backpressured PCM stream from the host to the robot."""

    def __init__(
        self,
        stream_id: int,
        write_callback: Callable[[AudioLiveStream, bytes, int], int],
        close_callback: Callable[[AudioLiveStream, int], None],
        abort_callback: Callable[[AudioLiveStream], None],
    ) -> None:
        self.stream_id = stream_id
        self._write_callback = write_callback
        self._close_callback = close_callback
        self._abort_callback = abort_callback
        self._sequence = 0
        self._closed = False
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def write(self, pcm_chunk: bytes) -> None:
        payload = bytes(pcm_chunk)
        if len(payload) % OUTPUT_AUDIO_FORMAT.sample_width_bytes != 0:
            raise ValueError("PCM chunk ends with a partial sample")
        with self._write_lock:
            with self._lock:
                if self._closed:
                    raise WatcheRobotError("audio live stream is closed")
                if not payload:
                    return
                sequence = self._sequence
            next_sequence = self._write_callback(self, payload, sequence)
            with self._lock:
                if not self._closed:
                    self._sequence = next_sequence

    def close(self) -> None:
        with self._write_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                sequence = self._sequence
            try:
                self._close_callback(self, sequence)
            except Exception:
                try:
                    self._abort_callback(self)
                except Exception:
                    pass
                raise

    def abort(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._abort_callback(self)

    def _mark_closed(self) -> None:
        with self._lock:
            self._closed = True

    def __enter__(self) -> AudioLiveStream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


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
