from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import wave
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

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
        expected_sha256: str | None,
        expected_duration_seconds: float,
        cancel_callback: Callable[[AudioPlayback], None],
    ) -> None:
        super().__init__(stream_id, transport, initial_state=JobState.STARTING)
        self.expected_sha256 = expected_sha256
        self.expected_duration_seconds = expected_duration_seconds
        self._cancel_callback = cancel_callback

    def cancel(self) -> None:
        if self.state.terminal:
            return
        self._cancel_callback(self)


class ContinuousAudioTransport(CommandTransport, Protocol):
    command_timeout: float

    def write_audio_stream(self, pcm: bytes, *, stream_id: int, last: bool = False) -> Future[None]: ...

    def close_audio_stream(self, stream_id: int) -> Future[None]: ...

    def audio_stream_first_frame_at(self, stream_id: int) -> float | None: ...


class ContinuousAudioPlayback(AudioPlayback):
    """Bounded incremental PCM writer. Await each write before the next one.

    The iterable belongs to the caller; only immutable PCM bytes cross to the
    transport loop. Completion is authoritative only after the final SHA matches.
    """

    def __init__(self, stream_id: int, transport: ContinuousAudioTransport, max_bytes: int,
                 cancel_callback: Callable[[AudioPlayback], None]) -> None:
        super().__init__(stream_id, transport, "", 0, cancel_callback)
        self.expected_sha256 = None
        self._stream_transport = transport
        self.max_bytes = max_bytes
        self.total_bytes = 0
        self._digest = hashlib.sha256()
        self._writer_lock = threading.Lock()
        self._write_future: Future[None] | None = None
        self._finished = False
        self.first_frame_sent_at: float | None = None

    def _check_writable(self) -> None:
        if self.state.terminal or self._finished:
            raise ValueError("continuous audio stream is no longer writable")
        if self._write_future is not None:
            if not self._write_future.done():
                raise ValueError("await the previous audio write before writing again")
            self._write_future.result()

    def _send(self, pcm: bytes, *, last: bool) -> Future[None]:
        future = self._stream_transport.write_audio_stream(pcm, stream_id=self.id, last=last)
        self._write_future = future
        def failed(completed: Future[None]) -> None:
            if completed.cancelled():
                return
            if completed.exception() is not None:
                self._update(JobState.FAILED, reason="audio_send_failed")
            elif self.first_frame_sent_at is None:
                get_first_frame = getattr(self._stream_transport, "audio_stream_first_frame_at", None)
                if get_first_frame is not None:
                    self.first_frame_sent_at = get_first_frame(self.id)
        future.add_done_callback(failed)
        return future

    def write(self, pcm: bytes) -> Future[None]:
        data = bytes(pcm)
        with self._writer_lock:
            self._check_writable()
            if not data or len(data) % 2:
                raise ValueError("continuous PCM must contain complete nonempty S16 samples")
            if self.total_bytes + len(data) > self.max_bytes:
                raise ValueError("continuous PCM exceeds max_bytes")
            self.total_bytes += len(data)
            self.expected_duration_seconds = self.total_bytes / 48000
            self._digest.update(data)
            return self._send(data, last=False)

    def cancel(self) -> None:
        if self.state is not JobState.COMPLETED:
            self._cancel_callback(self)

    def _close_sender(self) -> None:
        with self._writer_lock:
            self._finished = True
            if self._write_future is not None and not self._write_future.done():
                self._write_future.cancel()
        self._stream_transport.close_audio_stream(self.id).result(
            timeout=getattr(self._transport, "command_timeout", 5) + 1)

    def finish(self) -> Future[None]:
        with self._writer_lock:
            self._check_writable()
            if not self.total_bytes:
                raise ValueError("continuous PCM must not be empty")
            # Set the real checksum before LAST can trigger a terminal event.
            self.expected_sha256 = self._digest.hexdigest()
            self._finished = True
            return self._send(b"", last=True)


class ContinuousAudioSender:
    """Loop-local packet writer shared by authorized SDK transports."""

    def __init__(self, stream_id: int, take_credit: Callable[[int], Awaitable[None]],
                 send: Callable[[bytes], Awaitable[None]]) -> None:
        self.stream_id = stream_id
        self._take_credit, self._send = take_credit, send
        self.sequence = 0
        self.first_frame_sent_at: float | None = None
        self.finished = False
        self._lock = asyncio.Lock()
        self._pending = bytearray()

    def close(self) -> None:
        self.finished = True
        self._pending.clear()

    async def _send_packet(self, payload: bytes, *, last: bool = False) -> None:
        from .protocol import FLAG_FIRST, FLAG_LAST, FRAME_AUDIO, build_wspk
        await self._take_credit(self.stream_id)
        flags = FLAG_FIRST if self.sequence == 0 else 0
        if last:
            flags |= FLAG_LAST
        await self._send(build_wspk(FRAME_AUDIO, flags, self.stream_id, self.sequence, payload))
        if self.first_frame_sent_at is None:
            self.first_frame_sent_at = time.monotonic()
        self.sequence += 1

    async def write(self, pcm: bytes, *, last: bool) -> None:
        from .errors import WatcheRobotError
        async with self._lock:
            if self.finished or (not pcm and not last):
                raise ValueError("continuous audio sender is not writable")
            if last and self.sequence == 0 and not self._pending and not pcm:
                raise ValueError("continuous audio requires PCM before LAST")
            # Slots are fixed-size on the device. Preserve a sentence/chunk tail
            # until it fills one slot, rather than exhausting prefill credits with
            # arbitrarily small provider writes. Staging is always <= one slot.
            offset = 0
            while offset < len(pcm):
                if self.finished:
                    raise WatcheRobotError("continuous audio stream was closed")
                count = min(4096 - len(self._pending), len(pcm) - offset)
                self._pending.extend(memoryview(pcm)[offset:offset + count])
                offset += count
                if len(self._pending) == 4096:
                    payload = bytes(self._pending)
                    self._pending.clear()
                    await self._send_packet(payload)
            if last:
                if self._pending:
                    payload = bytes(self._pending)
                    self._pending.clear()
                    await self._send_packet(payload)
                await self._send_packet(b"", last=True)
                self.finished = True


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
