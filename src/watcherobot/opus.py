"""Application-side decoding for microphone Opus packets.

The Runtime/Daemon keeps device frames opaque while routing them.  This module
is deliberately used only by the high-level Application API, after a device
audio frame has reached the Application's Device channel.
"""

from __future__ import annotations

from threading import Lock
from typing import Any


class OpusDecodeError(RuntimeError):
    """An Opus packet from the device could not be decoded."""


class OpusDecoder:
    """Decode device Opus packets to 16 kHz mono signed-16-bit PCM."""

    def __init__(self) -> None:
        try:
            import av
        except ImportError as error:  # pragma: no cover - declared runtime dependency
            raise RuntimeError("microphone PCM support requires the 'av' package") from error

        self._av: Any = av
        self._decoder: Any = av.CodecContext.create("opus", "r")
        self._resampler: Any = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=16000,
        )
        self._flushed = False
        self._lock = Lock()

    def decode(self, packet: bytes) -> bytes:
        """Decode one complete device Opus packet.

        Device microphone frames contain exactly one Opus packet.  The result
        can be empty when the codec buffers samples internally; callers should
        concatenate successive frames and call :meth:`flush` at stream end.
        """

        with self._lock:
            if not packet:
                return b""
            if self._flushed:
                raise OpusDecodeError("cannot decode after the microphone stream has ended")
            try:
                frames = self._decoder.decode(self._av.Packet(bytes(packet)))
                return self._resample(frames)
            except Exception as error:
                raise OpusDecodeError("invalid Opus microphone packet") from error

    def flush(self) -> bytes:
        """Return delayed PCM samples after the device microphone stream ends."""

        with self._lock:
            if self._flushed:
                return b""
            self._flushed = True
            try:
                decoded = self._decoder.decode(None)
                pcm = bytearray(self._resample(decoded))
                pcm.extend(self._resample(self._resampler.resample(None), already_resampled=True))
                return bytes(pcm)
            except Exception as error:
                raise OpusDecodeError("could not flush the Opus microphone decoder") from error

    def _resample(self, frames: list[Any], *, already_resampled: bool = False) -> bytes:
        pcm = bytearray()
        for frame in frames:
            output_frames = [frame] if already_resampled else self._resampler.resample(frame)
            for output in output_frames:
                # PyAV exposes padded audio planes.  Only the bytes represented
                # by samples/channels belong in the PCM stream.
                pcm.extend(bytes(output.planes[0])[: output.samples * 2])
        return bytes(pcm)
