from __future__ import annotations

import queue
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .robot import WatcheRobot


class AudioPacketDecoder(Protocol):
    def decode(self, packet: bytes) -> bytes: ...

    def flush(self) -> bytes: ...


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
    decode_failures: int = 0

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
        if self.format.encoding != "pcm_s16le":
            raise ValueError("AudioRecording.save only supports pcm_s16le data")
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
        decoder: AudioPacketDecoder | None = None,
        queue_size: int = 32,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._robot = robot
        self._session_id = session_id
        self._format = audio_format or AudioFormat()
        self._decoder = decoder
        self._queue: queue.Queue[AudioFrame] = queue.Queue(maxsize=queue_size)
        self._dropped_frames = 0
        self._decode_failures = 0
        self._last_sequence = 0
        self._closed = False
        self._lock = Lock()
        self._decoder_lock = Lock()

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
    def decode_failures(self) -> int:
        with self._lock:
            return self._decode_failures

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
        try:
            self._robot._close_microphone(self._session_id)
        finally:
            self._finish_decoder_stream()

    def __enter__(self) -> MicrophoneSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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

    def _push_opus(self, packet: bytes, sequence: int) -> None:
        """Decode a device Opus packet before exposing it to an Application."""

        if self._decoder is None:
            raise RuntimeError("microphone session does not have an Opus decoder")

        # Decoder state must remain ordered with stream finalization.  A close
        # can race a WebSocket callback, so it waits for a packet already being
        # decoded to reach the queue before flush marks the session closed.
        with self._decoder_lock:
            with self._lock:
                if self._closed:
                    return
                self._last_sequence = sequence
            try:
                pcm = self._decoder.decode(packet)
            except Exception:
                with self._lock:
                    self._decode_failures += 1
                return
            if pcm:
                self._push(pcm, sequence)

    def _push_device_packet(self, packet: bytes, sequence: int) -> None:
        if self._decoder is None:
            self._push(packet, sequence)
            return
        self._push_opus(packet, sequence)

    def _flush_decoder_locked(self) -> None:
        if self._decoder is None:
            return
        try:
            pcm = self._decoder.flush()
        except Exception:
            with self._lock:
                self._decode_failures += 1
            return
        if not pcm:
            return
        frame = AudioFrame(bytes(pcm), self._last_sequence, time.time())
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self._dropped_frames += 1
            self._queue.put_nowait(frame)

    def _finish_decoder_stream(self) -> None:
        if self._decoder is None:
            with self._lock:
                self._closed = True
            return

        with self._decoder_lock:
            with self._lock:
                if self._closed:
                    return
            self._flush_decoder_locked()
            with self._lock:
                self._closed = True

    def _mark_remote_closed(self) -> None:
        self._finish_decoder_stream()
