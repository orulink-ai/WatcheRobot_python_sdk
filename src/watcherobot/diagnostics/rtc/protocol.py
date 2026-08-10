"""Strict watcher-rtc/1 control and WJPG packet validation."""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Literal, Mapping


RTC_PROTOCOL = "watcher-rtc/1"
MAX_CONTROL_BYTES = 20 * 1024
MAX_SDP_BYTES = 16 * 1024
MAX_CANDIDATE_BYTES = 2 * 1024
MAX_JPEG_BYTES = 60 * 1024
WJPG_HEADER_BYTES = 20

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
_DESKTOP_TYPES = frozenset(
    {
        "ctrl.rtc.session.start",
        "ctrl.rtc.signal",
        "ctrl.rtc.feedback",
        "ctrl.rtc.clock.ping",
        "ctrl.rtc.session.stop",
    }
)
_DEVICE_TYPES = frozenset(
    {
        "evt.rtc.capabilities",
        "evt.rtc.signal",
        "evt.rtc.state",
        "evt.rtc.clock.pong",
        "evt.rtc.stats",
    }
)
_COMMON_FIELDS = frozenset(
    {"type", "protocol", "client_id", "session_id", "command_id", "data"}
)


class RtcProtocolError(ValueError):
    """Raised when an RTC control or media packet violates its contract."""


@dataclass(frozen=True)
class RtcMessage:
    message_type: str
    client_id: str
    session_id: str
    command_id: str
    data: dict[str, object]


@dataclass(frozen=True)
class WjpgFrame:
    sequence: int
    timestamp_ms: int
    jpeg: bytes


def build_session_start(
    *, client_id: str, session_id: str, command_id: str, mode: str
) -> dict[str, object]:
    if mode not in {"av", "audio", "video"}:
        raise RtcProtocolError("mode must be av, audio, or video")
    envelope = _build_envelope(
        "ctrl.rtc.session.start",
        client_id=client_id,
        session_id=session_id,
        command_id=command_id,
        data={"mode": mode},
    )
    parse_rtc_message(envelope, direction="desktop")
    return envelope


def parse_rtc_message(
    payload: Mapping[str, object] | str | bytes,
    *,
    direction: Literal["desktop", "device"],
) -> RtcMessage:
    message = _decode_object(payload)
    _require_exact_fields(message, _COMMON_FIELDS)
    message_type = _require_string(message, "type")
    allowed = _DESKTOP_TYPES if direction == "desktop" else _DEVICE_TYPES
    if message_type not in allowed:
        raise RtcProtocolError(f"unsupported RTC message type: {message_type}")
    if _require_string(message, "protocol") != RTC_PROTOCOL:
        raise RtcProtocolError(f"protocol must be {RTC_PROTOCOL}")
    client_id = _require_id(message, "client_id")
    session_id = _require_id(message, "session_id")
    command_id = _require_id(message, "command_id")
    raw_data = message.get("data")
    if not isinstance(raw_data, Mapping):
        raise RtcProtocolError("data must be an object")
    data = dict(raw_data)
    _validate_data(message_type, data)
    return RtcMessage(message_type, client_id, session_id, command_id, data)


def decode_wjpg_frame(packet: bytes) -> WjpgFrame:
    if len(packet) < WJPG_HEADER_BYTES:
        raise RtcProtocolError("WJPG packet is truncated")
    magic, version, flags, header_size, sequence, timestamp_ms, jpeg_size = (
        struct.unpack_from("<4sBBHIII", packet)
    )
    if magic != b"WJPG" or version != 1 or flags != 0:
        raise RtcProtocolError("WJPG header is unsupported")
    if header_size != WJPG_HEADER_BYTES:
        raise RtcProtocolError("WJPG header length is invalid")
    if jpeg_size == 0 or jpeg_size > MAX_JPEG_BYTES:
        raise RtcProtocolError("JPEG length is outside the accepted range")
    jpeg = packet[WJPG_HEADER_BYTES:]
    if len(jpeg) != jpeg_size or jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
        raise RtcProtocolError("JPEG payload is incomplete")
    return WjpgFrame(sequence=sequence, timestamp_ms=timestamp_ms, jpeg=jpeg)


def _build_envelope(
    message_type: str,
    *,
    client_id: str,
    session_id: str,
    command_id: str,
    data: dict[str, object],
) -> dict[str, object]:
    return {
        "type": message_type,
        "protocol": RTC_PROTOCOL,
        "client_id": client_id,
        "session_id": session_id,
        "command_id": command_id,
        "data": data,
    }


def _decode_object(
    payload: Mapping[str, object] | str | bytes,
) -> dict[str, object]:
    if isinstance(payload, bytes):
        if len(payload) > MAX_CONTROL_BYTES:
            raise RtcProtocolError("control payload exceeds maximum length")
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RtcProtocolError("control payload must be UTF-8") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_CONTROL_BYTES:
            raise RtcProtocolError("control payload exceeds maximum length")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RtcProtocolError("control payload must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RtcProtocolError("control payload must be an object")
    return dict(payload)


def _require_exact_fields(
    payload: Mapping[str, object], expected: frozenset[str] | set[str]
) -> None:
    unknown = sorted(set(payload) - expected)
    missing = sorted(expected - set(payload))
    if unknown:
        raise RtcProtocolError(f"unknown fields: {', '.join(unknown)}")
    if missing:
        raise RtcProtocolError(f"missing fields: {', '.join(missing)}")


def _require_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise RtcProtocolError(f"{field} must be a string")
    return value


def _require_id(payload: Mapping[str, object], field: str) -> str:
    value = _require_string(payload, field)
    if _ID.fullmatch(value) is None:
        raise RtcProtocolError(f"{field} has invalid format")
    return value


def _validate_data(message_type: str, data: dict[str, object]) -> None:
    if message_type == "ctrl.rtc.session.start":
        _require_exact_fields(data, {"mode"})
        if data.get("mode") not in {"av", "audio", "video"}:
            raise RtcProtocolError("mode must be av, audio, or video")
        return
    if message_type == "ctrl.rtc.session.stop":
        _require_exact_fields(data, set())
        return
    if message_type in {"ctrl.rtc.signal", "evt.rtc.signal"}:
        kind = data.get("kind")
        if kind in {"offer", "answer"}:
            _require_exact_fields(data, {"kind", "sdp"})
            sdp = data.get("sdp")
            if not isinstance(sdp, str) or len(sdp.encode("utf-8")) > MAX_SDP_BYTES:
                raise RtcProtocolError("SDP exceeds the 16 KiB limit")
        elif kind == "candidate":
            _require_exact_fields(
                data,
                {"kind", "candidate", "sdp_mid", "sdp_mline_index"},
            )
            candidate = data.get("candidate")
            if not isinstance(candidate, str) or len(candidate.encode("utf-8")) > MAX_CANDIDATE_BYTES:
                raise RtcProtocolError("candidate exceeds the 2 KiB limit")
            if not isinstance(data.get("sdp_mid"), str) or type(data.get("sdp_mline_index")) is not int:
                raise RtcProtocolError("candidate metadata is invalid")
        elif kind == "bye":
            _require_exact_fields(data, {"kind"})
        else:
            raise RtcProtocolError("signal kind is invalid")
        return
    if message_type == "ctrl.rtc.feedback":
        fields = {
            "display_fps_x100",
            "frame_age_p95_us",
            "rtt_us",
            "audio_queue_ms",
            "audio_packet_loss_x100",
            "audio_jitter_us",
            "audio_concealed_frames",
            "congestion_level",
        }
        _require_exact_fields(data, fields)
        congestion_level = 0
        for field in fields:
            value = _require_nonnegative_int(data, field)
            if field == "congestion_level":
                congestion_level = value
        if congestion_level > 3:
            raise RtcProtocolError("congestion_level must be between 0 and 3")
        return
    if message_type == "ctrl.rtc.clock.ping":
        _require_exact_fields(data, {"browser_send_us"})
        _require_nonnegative_int(data, "browser_send_us", positive=True)
        return
    if message_type == "evt.rtc.clock.pong":
        fields = {"browser_send_us", "device_receive_us", "device_send_us"}
        _require_exact_fields(data, fields)
        for field in fields:
            _require_nonnegative_int(data, field)
        return
    if message_type == "evt.rtc.state":
        allowed = {"state", "reason"}
        _require_exact_fields(data, {"state"} if "reason" not in data else allowed)
        state = data.get("state")
        if not isinstance(state, str) or state not in {
            "starting",
            "signaling",
            "connected",
            "stopping",
            "stopped",
            "failed",
        }:
            raise RtcProtocolError("RTC state is invalid")
        if "reason" in data and not isinstance(data["reason"], str):
            raise RtcProtocolError("reason must be a string")
        return
    if message_type == "evt.rtc.capabilities":
        fields = {
            "sta_ip",
            "firmware_commit",
            "firmware_dirty",
            "video",
            "audio",
            "data_channel",
            "stress_supported",
        }
        _require_exact_fields(data, fields)
        if (
            not isinstance(data["sta_ip"], str)
            or not isinstance(data["firmware_commit"], str)
            or not isinstance(data["firmware_dirty"], bool)
            or not isinstance(data["stress_supported"], bool)
        ):
            raise RtcProtocolError("RTC capabilities are invalid")
        raw_video = data["video"]
        raw_audio = data["audio"]
        raw_channel = data["data_channel"]
        if not isinstance(raw_video, Mapping):
            raise RtcProtocolError("video capability must be an object")
        if not isinstance(raw_audio, Mapping):
            raise RtcProtocolError("audio capability must be an object")
        if not isinstance(raw_channel, Mapping):
            raise RtcProtocolError("data_channel capability must be an object")
        video = dict(raw_video)
        audio = dict(raw_audio)
        channel = dict(raw_channel)
        _require_exact_fields(
            video,
            {"codec", "width", "height", "min_fps", "max_fps", "max_jpeg_bytes"},
        )
        _require_exact_fields(audio, {"codec", "sample_rate", "channels"})
        _require_exact_fields(
            channel,
            {
                "label",
                "ordered",
                "max_packet_lifetime_ms",
                "send_cache_bytes",
                "receive_cache_bytes",
                "cache_timeout_ms",
            },
        )
        for field in ("width", "height", "min_fps", "max_fps", "max_jpeg_bytes"):
            _require_nonnegative_int(video, field, positive=True)
        for field in ("sample_rate", "channels"):
            _require_nonnegative_int(audio, field, positive=True)
        for field in (
            "max_packet_lifetime_ms",
            "send_cache_bytes",
            "receive_cache_bytes",
            "cache_timeout_ms",
        ):
            _require_nonnegative_int(channel, field, positive=True)
        if type(channel["ordered"]) is not bool:
            raise RtcProtocolError("DataChannel ordered flag must be boolean")
        if video != {
            "codec": "MJPEG",
            "width": 640,
            "height": 480,
            "min_fps": 8,
            "max_fps": 12,
            "max_jpeg_bytes": MAX_JPEG_BYTES,
        }:
            raise RtcProtocolError("video capability is unsupported")
        if audio != {"codec": "G711A", "sample_rate": 8000, "channels": 1}:
            raise RtcProtocolError("audio capability is unsupported")
        if channel != {
            "label": "mjpeg-data",
            "ordered": False,
            "max_packet_lifetime_ms": 200,
            "send_cache_bytes": 192 * 1024,
            "receive_cache_bytes": 128 * 1024,
            "cache_timeout_ms": 250,
        }:
            raise RtcProtocolError("DataChannel capability is unsupported")
        return
    if message_type == "evt.rtc.stats":
        fields = {
            "state",
            "jpeg_average_bytes",
            "source_fps_x100",
            "target_fps",
            "sent_fps_x100",
            "source_frames",
            "sent_frames",
            "dropped_frames",
            "oversized_frames",
            "rate_limited_frames",
            "video_send_p95_us",
            "video_send_max_us",
            "audio_queue_ms",
            "audio_packets",
            "audio_queue_dropped",
            "audio_render_errors",
            "stress_rx_bytes",
            "free_heap_bytes",
        }
        _require_exact_fields(data, fields)
        if data.get("state") not in {"connecting", "connected"}:
            raise RtcProtocolError("stats state is invalid")
        for field in fields - {"state"}:
            _require_nonnegative_int(data, field)
        return


def _require_nonnegative_int(
    payload: Mapping[str, object], field: str, *, positive: bool = False
) -> int:
    value = payload.get(field)
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise RtcProtocolError(f"{field} must be a {qualifier} integer")
    return value
