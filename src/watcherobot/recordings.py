"""Device-side reliable recording API and WREC v1 media tooling."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import struct
import threading
import time
import wave
import zlib
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal

from .errors import WatcheRobotError
from .protocol import FLAG_CANCEL, FLAG_LAST, FRAME_RECORDING, BinaryFrame

RecordingMode = Literal["video", "audio", "av"]
RECORDING_CAPABILITY = "recording.device.v1"
RECORDING_AV_CAPABILITY = "recording.device.av.v1"
DOWNLOAD_RESUME_CAPABILITY = "recording.download.resume.v1"
WREC_HEADER = struct.Struct("<4sBBHIQII")
WREC_MAGIC = b"WREC"
WREC_VERSION = 1
WREC_JPEG = 1
WREC_PCM = 2


@dataclass(frozen=True)
class RecordingInfo:
    id: str
    mode: RecordingMode
    state: str
    duration_ms: int = 0
    bytes: int = 0
    segments: tuple[dict[str, Any], ...] = ()
    sha256: str | None = None
    stop_reason: str | None = None
    created_at: str | None = None
    width: int = 0
    height: int = 0
    fps: int = 0
    quality: int = 0
    estimated_remaining_ms: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RecordingInfo":
        recording_id = payload.get("recording_id", payload.get("id"))
        mode = payload.get("mode")
        state = payload.get("state")
        if not isinstance(recording_id, str) or not recording_id:
            raise WatcheRobotError("recording response is missing recording_id")
        if mode not in {"video", "audio", "av"}:
            raise WatcheRobotError("recording response contains an invalid mode")
        if not isinstance(state, str):
            raise WatcheRobotError("recording response is missing state")
        segments = payload.get("segments", [])
        return cls(
            id=recording_id,
            mode=mode,
            state=state,
            duration_ms=max(0, int(payload.get("duration_ms", 0))),
            bytes=max(0, int(payload.get("bytes", payload.get("byte_count", 0)))),
            segments=tuple(dict(item) for item in segments if isinstance(item, dict)) if isinstance(segments, list) else (),
            sha256=payload.get("sha256") if isinstance(payload.get("sha256"), str) else None,
            stop_reason=payload.get("stop_reason") if isinstance(payload.get("stop_reason"), str) else None,
            created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
            width=max(0, int(payload.get("width", 0))),
            height=max(0, int(payload.get("height", 0))),
            fps=max(0, int(payload.get("fps", 0))),
            quality=max(0, int(payload.get("quality", 0))),
            estimated_remaining_ms=max(0, int(payload.get("estimated_remaining_ms", 0))),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "mode": self.mode, "state": self.state,
            "duration_ms": self.duration_ms, "bytes": self.bytes,
            "segments": list(self.segments), "sha256": self.sha256,
            "stop_reason": self.stop_reason, "created_at": self.created_at,
            "width": self.width, "height": self.height, "fps": self.fps,
            "quality": self.quality, "estimated_remaining_ms": self.estimated_remaining_ms,
        }


@dataclass
class DeviceRecording:
    info: RecordingInfo
    _domain: "RecordingsDomain" = field(repr=False)

    @property
    def id(self) -> str:
        return self.info.id

    def status(self) -> RecordingInfo:
        self.info = self._domain.status(self.id)
        return self.info

    def stop(self) -> RecordingInfo:
        self.info = self._domain.stop(self.id)
        return self.info


@dataclass(frozen=True)
class RecordingDownload:
    recording: RecordingInfo
    raw_directory: Path
    output_path: Path | None
    resumed_bytes: int
    downloaded_bytes: int


@dataclass(frozen=True)
class WrecRecord:
    kind: int
    flags: int
    sequence: int
    timestamp_us: int
    payload: bytes


def encode_wrec_record(record: WrecRecord) -> bytes:
    payload = bytes(record.payload)
    return WREC_HEADER.pack(
        WREC_MAGIC, WREC_VERSION, record.kind, record.flags,
        record.sequence, record.timestamp_us, len(payload), zlib.crc32(payload) & 0xFFFFFFFF,
    ) + payload


def iter_wrec_records(paths: Iterable[str | Path], *, tolerate_truncated_tail: bool = False) -> Iterator[WrecRecord]:
    expected: dict[int, int] = {}
    for source in paths:
        path = Path(source)
        with path.open("rb") as stream:
            while True:
                header = stream.read(WREC_HEADER.size)
                if not header:
                    break
                if len(header) != WREC_HEADER.size:
                    if tolerate_truncated_tail:
                        break
                    raise ValueError(f"truncated WREC header in {path}")
                magic, version, kind, flags, sequence, timestamp_us, length, checksum = WREC_HEADER.unpack(header)
                if magic != WREC_MAGIC or version != WREC_VERSION or kind not in {WREC_JPEG, WREC_PCM}:
                    raise ValueError(f"invalid WREC record in {path}")
                payload = stream.read(length)
                if len(payload) != length:
                    if tolerate_truncated_tail:
                        break
                    raise ValueError(f"truncated WREC payload in {path}")
                if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
                    raise ValueError(f"WREC CRC32 mismatch in {path} at sequence {sequence}")
                previous = expected.get(kind)
                if previous is not None and sequence != previous:
                    raise ValueError(f"WREC {kind} sequence gap: expected {previous}, got {sequence}")
                expected[kind] = (sequence + 1) & 0xFFFFFFFF
                yield WrecRecord(kind, flags, sequence, timestamp_us, payload)


def write_pcm_wav(records: Iterable[WrecRecord], output_path: str | Path, *, sample_rate_hz: int = 16000) -> Path:
    target = Path(output_path)
    partial = target.with_name(target.name + ".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(partial), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate_hz)
        for record in records:
            if record.kind == WREC_PCM:
                output.writeframesraw(record.payload)
    os.replace(partial, target)
    return target


def mux_wrec_to_mp4(records: Iterable[WrecRecord], output_path: str | Path, *, width: int, height: int, fps: int = 5, with_audio: bool = False) -> Path:
    """Encode timestamped JPEG/PCM records into browser-compatible MP4."""
    import av

    target = Path(output_path)
    partial = target.with_name(target.name + ".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(records, key=lambda item: (item.timestamp_us, item.kind, item.sequence))
    container = av.open(str(partial), mode="w", format="mp4")
    try:
        video = container.add_stream("libx264", rate=fps)
        video.width = width
        video.height = height
        video.pix_fmt = "yuv420p"
        audio = container.add_stream("aac", rate=16000) if with_audio else None
        if audio is not None:
            audio.layout = "mono"
        media_start_us = min((item.timestamp_us for item in items), default=0)
        decoder = av.CodecContext.create("mjpeg", "r")
        for item in items:
            relative_us = max(0, item.timestamp_us - media_start_us)
            if item.kind == WREC_JPEG:
                packet = av.Packet(item.payload)
                frames = decoder.decode(packet)  # type: ignore[attr-defined]
                for frame in frames:
                    converted = frame.reformat(width=width, height=height, format="yuv420p")
                    converted.pts = relative_us
                    converted.time_base = Fraction(1, 1_000_000)
                    for encoded in video.encode(converted):
                        container.mux(encoded)
            elif item.kind == WREC_PCM and audio is not None:
                frame = av.AudioFrame(format="s16", layout="mono", samples=len(item.payload) // 2)
                frame.sample_rate = 16000
                frame.pts = round(relative_us * 16000 / 1_000_000)
                frame.time_base = Fraction(1, 16000)
                frame.planes[0].update(item.payload)
                for encoded in audio.encode(frame):
                    container.mux(encoded)
        for encoded in video.encode():
            container.mux(encoded)
        if audio is not None:
            for encoded in audio.encode():
                container.mux(encoded)
    finally:
        container.close()
    os.replace(partial, target)
    return target


class _DownloadStream:
    def __init__(self, stream_id: int) -> None:
        self.stream_id = stream_id
        self.frames: queue.Queue[BinaryFrame] = queue.Queue(maxsize=64)


class RecordingsDomain:
    def __init__(self, robot: Any) -> None:
        self._robot = robot
        self._downloads: dict[int, _DownloadStream] = {}
        self._lock = threading.Lock()
        self._next_stream_id = 1

    def _reserve_download(self) -> _DownloadStream:
        with self._lock:
            for _ in range(65535):
                stream_id = self._next_stream_id
                self._next_stream_id = 1 if stream_id == 65535 else stream_id + 1
                if stream_id not in self._downloads:
                    receiver = _DownloadStream(stream_id)
                    self._downloads[stream_id] = receiver
                    return receiver
        raise WatcheRobotError("no recording download stream is available")

    def start(self, mode: RecordingMode = "video", *, width: int = 640, height: int = 480, fps: int = 5, quality: int = 80, duration: float | None = None) -> DeviceRecording:
        if mode not in {"video", "audio", "av"}:
            raise ValueError("mode must be video, audio, or av")
        self._robot._require_capability(RECORDING_CAPABILITY)
        if mode == "av":
            self._robot._require_capability(RECORDING_AV_CAPABILITY)
        if not 1 <= fps <= 10 or not 1 <= quality <= 100 or width <= 0 or height <= 0:
            raise ValueError("invalid recording dimensions, fps, or quality")
        if duration is not None and duration <= 0:
            raise ValueError("duration must be positive")
        payload: dict[str, Any] = {"mode": mode, "width": width, "height": height, "fps": fps, "quality": quality}
        if duration is not None:
            payload["duration_ms"] = round(duration * 1000)
        response = self._robot._command("ctrl.recording.start", payload, timeout=10.0)
        return DeviceRecording(RecordingInfo.from_payload(response.get("data", {})), self)

    def stop(self, recording_id: str) -> RecordingInfo:
        response = self._robot._command("ctrl.recording.stop", {"recording_id": recording_id}, timeout=30.0)
        info = RecordingInfo.from_payload(response.get("data", {}))
        deadline = time.monotonic() + 30.0
        while info.state in {"recording", "finalizing"} and time.monotonic() < deadline:
            time.sleep(0.1)
            info = self.status(recording_id)
        if info.state in {"recording", "finalizing"}:
            raise TimeoutError(f"recording {recording_id} did not finish finalizing")
        return info

    def heartbeat(self, recording_id: str) -> None:
        self._robot._command("ctrl.recording.heartbeat", {"recording_id": recording_id})

    def list(self) -> list[RecordingInfo]:
        response = self._robot._command("recording.list", {})
        items = response.get("data", {}).get("recordings", [])
        if not isinstance(items, list):
            raise WatcheRobotError("recording.list returned an invalid list")
        return [RecordingInfo.from_payload(item) for item in items if isinstance(item, dict)]

    def status(self, recording_id: str | None = None) -> RecordingInfo:
        payload = {} if recording_id is None else {"recording_id": recording_id}
        response = self._robot._command("recording.status", payload)
        return RecordingInfo.from_payload(response.get("data", {}))

    def delete(self, recording_id: str) -> None:
        self._robot._command("recording.delete", {"recording_id": recording_id})

    def download(self, recording_id: str, raw_directory: str | Path, *, progress: Callable[[dict[str, Any]], None] | None = None) -> RecordingDownload:
        info = self.status(recording_id)
        target = Path(raw_directory)
        target.mkdir(parents=True, exist_ok=True)
        manifest_path = target / "manifest.json"
        manifest_partial = target / "manifest.json.part"
        manifest_partial.write_text(json.dumps(info.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(manifest_partial, manifest_path)
        resumed = 0
        downloaded = 0
        segments = info.segments or ({"index": 0, "name": "segment-0000.wrec", "bytes": info.bytes, "sha256": info.sha256},)
        for ordinal, segment in enumerate(segments):
            name = str(segment.get("name") or f"segment-{ordinal:04d}.wrec")
            final = target / Path(name).name
            partial = final.with_name(final.name + ".part")
            expected_bytes = int(segment.get("bytes", 0))
            expected_sha = segment.get("sha256")
            if final.exists():
                final_size = final.stat().st_size
                valid_size = not expected_bytes or final_size == expected_bytes
                valid_hash = not isinstance(expected_sha, str) or hashlib.sha256(final.read_bytes()).hexdigest() == expected_sha
                if valid_size and valid_hash:
                    resumed += final_size
                    continue
                os.replace(final, partial)
            offset = partial.stat().st_size if partial.exists() else 0
            if (expected_bytes and offset > expected_bytes) or (
                expected_bytes and offset == expected_bytes and isinstance(expected_sha, str)
                and hashlib.sha256(partial.read_bytes()).hexdigest() != expected_sha
            ):
                partial.unlink()
                offset = 0
            resumed += offset
            remaining = max(0, expected_bytes - offset)
            if remaining and shutil.disk_usage(target).free < remaining:
                raise WatcheRobotError(f"insufficient local disk space for {name}")
            receiver = self._reserve_download()
            stream_id = receiver.stream_id
            next_sequence = 0
            try:
                response = self._robot._command(
                    "recording.download.begin",
                    {
                        "recording_id": recording_id,
                        "segment": int(segment.get("index", ordinal)),
                        "offset": offset,
                        "stream_id": stream_id,
                    },
                )
                acknowledged_stream = response.get("data", {}).get("stream_id")
                if acknowledged_stream != stream_id:
                    raise WatcheRobotError("device did not accept the requested download stream_id")
                with partial.open("ab") as output:
                    while True:
                        try:
                            frame = receiver.frames.get(timeout=10.0)
                        except queue.Empty as error:
                            raise TimeoutError("recording download stalled") from error
                        if frame.sequence != next_sequence:
                            raise WatcheRobotError(f"recording download sequence mismatch: expected {next_sequence}, got {frame.sequence}")
                        if frame.flags & FLAG_CANCEL:
                            raise WatcheRobotError("recording download was cancelled by the device")
                        output.write(frame.payload)
                        output.flush()
                        downloaded += len(frame.payload)
                        next_sequence += 1
                        if progress:
                            progress({"recording_id": recording_id, "segment": ordinal, "bytes": output.tell(), "total_bytes": expected_bytes})
                        if frame.flags & FLAG_LAST:
                            break
            except BaseException:
                try:
                    self._robot._command("recording.download.cancel", {"stream_id": stream_id})
                except Exception:
                    pass
                raise
            finally:
                with self._lock:
                    self._downloads.pop(stream_id, None)
            digest = hashlib.sha256(partial.read_bytes()).hexdigest()
            if isinstance(expected_sha, str) and digest != expected_sha:
                raise WatcheRobotError(f"SHA-256 mismatch for {name}")
            os.replace(partial, final)
        if info.sha256:
            aggregate = hashlib.sha256()
            for ordinal, segment in enumerate(segments):
                name = str(segment.get("name") or f"segment-{ordinal:04d}.wrec")
                with (target / Path(name).name).open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        aggregate.update(chunk)
            if aggregate.hexdigest() != info.sha256:
                raise WatcheRobotError("recording aggregate SHA-256 mismatch")
        return RecordingDownload(info, target, None, resumed, downloaded)

    def _on_binary(self, frame: BinaryFrame) -> bool:
        if frame.frame_type != FRAME_RECORDING:
            return False
        with self._lock:
            receiver = self._downloads.get(frame.stream_id)
        if receiver is not None:
            try:
                receiver.frames.put_nowait(frame)
            except queue.Full:
                # A dropped download frame must never look like success. Insert
                # an impossible sequence marker so the consumer fails closed.
                try:
                    receiver.frames.get_nowait()
                except queue.Empty:
                    pass
                receiver.frames.put_nowait(
                    BinaryFrame(FRAME_RECORDING, FLAG_LAST, frame.stream_id, 0xFFFFFFFF, b"")
                )
        return True
