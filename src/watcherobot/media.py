from __future__ import annotations

import queue
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .robot import WatcheRobot


@dataclass(frozen=True)
class AudioFormat:
    sample_rate_hz: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2
    encoding: str = "pcm_s16le"


@dataclass(frozen=True)
class AudioFrame:
    data: bytes
    sequence: int
    timestamp: float


@dataclass(frozen=True)
class AudioRecording:
    data: bytes
    format: AudioFormat
    dropped_frames: int = 0

    @property
    def duration_seconds(self) -> float:
        bytes_per_second = (
            self.format.sample_rate_hz
            * self.format.channels
            * self.format.sample_width_bytes
        )
        if bytes_per_second <= 0:
            return 0.0
        return len(self.data) / bytes_per_second

    def save(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(self.format.channels)
            wav_file.setsampwidth(self.format.sample_width_bytes)
            wav_file.setframerate(self.format.sample_rate_hz)
            wav_file.writeframes(self.data)
        return output


@dataclass(frozen=True)
class ImageFrame:
    data: bytes
    sequence: int
    timestamp: float
    content_type: str = "image/jpeg"
    session_id: int = 0

    def save(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.data)
        return output


class MicrophoneSession:
    def __init__(
        self,
        robot: WatcheRobot,
        session_id: int,
        *,
        audio_format: AudioFormat | None = None,
        queue_size: int = 32,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._robot = robot
        self._session_id = session_id
        self._format = audio_format or AudioFormat()
        self._queue: queue.Queue[AudioFrame] = queue.Queue(maxsize=queue_size)
        self._dropped_frames = 0
        self._closed = False
        self._lock = Lock()

    @property
    def id(self) -> int:
        return self._session_id

    @property
    def format(self) -> AudioFormat:
        return self._format

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def read(self, timeout: float | None = None) -> AudioFrame:
        if self.closed and self._queue.empty():
            raise RuntimeError("microphone session is closed")
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("no microphone frame before timeout") from error

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._robot._close_microphone(self._session_id)

    def __enter__(self) -> MicrophoneSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _push(self, data: bytes, sequence: int) -> None:
        with self._lock:
            if self._closed:
                return
        frame = AudioFrame(bytes(data), sequence, time.time())
        try:
            self._queue.put_nowait(frame)
            return
        except queue.Full:
            pass
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self._dropped_frames += 1
        self._queue.put_nowait(frame)

    def _mark_remote_closed(self) -> None:
        with self._lock:
            self._closed = True
