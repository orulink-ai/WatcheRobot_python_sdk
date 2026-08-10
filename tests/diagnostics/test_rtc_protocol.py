from __future__ import annotations

import struct

import pytest

from watcherobot.diagnostics.rtc.metrics import percentile
from watcherobot.diagnostics.rtc.protocol import (
    MAX_CANDIDATE_BYTES,
    MAX_SDP_BYTES,
    RTC_PROTOCOL,
    RtcProtocolError,
    build_session_start,
    decode_wjpg_frame,
    parse_rtc_message,
)


CLIENT_ID = "desktop-0123456789abcdef"
SESSION_ID = "session-0123456789abcdef"
COMMAND_ID = "command-0123456789abcdef"


def test_session_start_has_stable_versioned_envelope() -> None:
    assert build_session_start(
        client_id=CLIENT_ID,
        session_id=SESSION_ID,
        command_id=COMMAND_ID,
        mode="av",
    ) == {
        "type": "ctrl.rtc.session.start",
        "protocol": RTC_PROTOCOL,
        "client_id": CLIENT_ID,
        "session_id": SESSION_ID,
        "command_id": COMMAND_ID,
        "data": {"mode": "av"},
    }


def test_protocol_rejects_unknown_fields_and_oversized_signaling() -> None:
    base = build_session_start(
        client_id=CLIENT_ID,
        session_id=SESSION_ID,
        command_id=COMMAND_ID,
        mode="video",
    )
    base["unexpected"] = True
    with pytest.raises(RtcProtocolError, match="unknown fields"):
        parse_rtc_message(base, direction="desktop")

    offer = {
        "type": "ctrl.rtc.signal",
        "protocol": RTC_PROTOCOL,
        "client_id": CLIENT_ID,
        "session_id": SESSION_ID,
        "command_id": COMMAND_ID,
        "data": {"kind": "offer", "sdp": "x" * (MAX_SDP_BYTES + 1)},
    }
    with pytest.raises(RtcProtocolError, match="SDP"):
        parse_rtc_message(offer, direction="desktop")

    candidate = {
        **offer,
        "data": {
            "kind": "candidate",
            "candidate": "x" * (MAX_CANDIDATE_BYTES + 1),
            "sdp_mid": "0",
            "sdp_mline_index": 0,
        },
    }
    with pytest.raises(RtcProtocolError, match="candidate"):
        parse_rtc_message(candidate, direction="desktop")


def test_wjpg_v1_decoder_validates_header_and_jpeg_boundaries() -> None:
    jpeg = b"\xff\xd8payload\xff\xd9"
    packet = struct.pack("<4sBBHIII", b"WJPG", 1, 0, 20, 7, 1234, len(jpeg)) + jpeg
    frame = decode_wjpg_frame(packet)
    assert frame.sequence == 7
    assert frame.timestamp_ms == 1234
    assert frame.jpeg == jpeg

    with pytest.raises(RtcProtocolError, match="JPEG"):
        decode_wjpg_frame(packet[:-1])


def test_feedback_and_clock_sync_are_strict_and_use_integer_microseconds() -> None:
    feedback = {
        "type": "ctrl.rtc.feedback",
        "protocol": RTC_PROTOCOL,
        "client_id": CLIENT_ID,
        "session_id": SESSION_ID,
        "command_id": COMMAND_ID,
        "data": {
            "display_fps_x100": 1000,
            "frame_age_p95_us": 180_000,
            "rtt_us": 40_000,
            "audio_queue_ms": 20,
            "audio_packet_loss_x100": 125,
            "audio_jitter_us": 2_000,
            "audio_concealed_frames": 0,
            "congestion_level": 1,
        },
    }
    assert parse_rtc_message(feedback, direction="desktop").data["rtt_us"] == 40_000

    feedback["data"] = {**feedback["data"], "legacy_rtt_ms": 40}
    with pytest.raises(RtcProtocolError, match="unknown fields"):
        parse_rtc_message(feedback, direction="desktop")

    ping = {
        **feedback,
        "type": "ctrl.rtc.clock.ping",
        "data": {"browser_send_us": 123_456},
    }
    assert parse_rtc_message(ping, direction="desktop").data == {
        "browser_send_us": 123_456
    }


def test_device_state_and_clock_events_validate_the_same_session_envelope() -> None:
    state = {
        "type": "evt.rtc.state",
        "protocol": RTC_PROTOCOL,
        "client_id": CLIENT_ID,
        "session_id": SESSION_ID,
        "command_id": COMMAND_ID,
        "data": {"state": "connected"},
    }
    assert parse_rtc_message(state, direction="device").data == {"state": "connected"}

    pong = {
        **state,
        "type": "evt.rtc.clock.pong",
        "data": {
            "browser_send_us": 100,
            "device_receive_us": 200,
            "device_send_us": 210,
        },
    }
    assert parse_rtc_message(pong, direction="device").data["device_send_us"] == 210


def test_device_capabilities_record_firmware_and_data_channel_contract() -> None:
    capabilities = {
        "type": "evt.rtc.capabilities",
        "protocol": RTC_PROTOCOL,
        "client_id": CLIENT_ID,
        "session_id": SESSION_ID,
        "command_id": COMMAND_ID,
        "data": {
            "sta_ip": "192.168.1.23",
            "firmware_commit": "0123456789ab",
            "firmware_dirty": False,
            "video": {
                "codec": "MJPEG",
                "width": 640,
                "height": 480,
                "min_fps": 8,
                "max_fps": 12,
                "max_jpeg_bytes": 60 * 1024,
            },
            "audio": {"codec": "G711A", "sample_rate": 8000, "channels": 1},
            "data_channel": {
                "label": "mjpeg-data",
                "ordered": False,
                "max_packet_lifetime_ms": 200,
                "send_cache_bytes": 192 * 1024,
                "receive_cache_bytes": 128 * 1024,
                "cache_timeout_ms": 250,
            },
            "stress_supported": True,
        },
    }
    parsed = parse_rtc_message(capabilities, direction="device")
    assert parsed.data["firmware_commit"] == "0123456789ab"


def test_percentile_uses_nearest_rank_and_handles_empty_samples() -> None:
    assert percentile([], 95) == 0.0
    assert percentile([10, 20, 30, 40], 95) == 40.0
    assert percentile([40, 10, 20, 30], 50) == 20.0
