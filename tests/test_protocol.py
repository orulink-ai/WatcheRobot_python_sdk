import json
import struct

import pytest

from watcherobot.protocol import (
    FRAME_AUDIO,
    FRAME_IMAGE,
    FLAG_FIRST,
    FLAG_LAST,
    ProtocolError,
    build_command,
    build_wspk,
    parse_wspk,
)


def test_build_command_uses_existing_envelope_and_command_id():
    message = build_command("ctrl.behavior.play", {"behavior_id": "greeting", "repeat": 2}, "cmd-1")

    assert json.loads(message) == {
        "type": "ctrl.behavior.play",
        "code": 0,
        "data": {"behavior_id": "greeting", "repeat": 2, "command_id": "cmd-1"},
    }


def test_parse_legacy_wspk_audio_frame():
    payload = b"\x01\x02\x03\x04"
    packet = b"WSPK" + bytes([FRAME_AUDIO, FLAG_FIRST]) + struct.pack("<II", 7, len(payload)) + payload

    frame = parse_wspk(packet)

    assert frame.frame_type == FRAME_AUDIO
    assert frame.flags == FLAG_FIRST
    assert frame.stream_id == 0
    assert frame.sequence == 7
    assert frame.payload == payload


def test_parse_current_wspk_image_frame():
    payload = b"jpeg"
    packet = (
        b"WSPK"
        + bytes([FRAME_IMAGE, FLAG_FIRST | FLAG_LAST])
        + struct.pack("<HII", 23, 9, len(payload))
        + payload
    )

    frame = parse_wspk(packet)

    assert frame.stream_id == 23
    assert frame.sequence == 9
    assert frame.payload == payload


def test_build_current_wspk_audio_frame_round_trips():
    packet = build_wspk(FRAME_AUDIO, FLAG_FIRST | FLAG_LAST, 23, 7, b"pcm")

    frame = parse_wspk(packet)

    assert frame.frame_type == FRAME_AUDIO
    assert frame.flags == FLAG_FIRST | FLAG_LAST
    assert frame.stream_id == 23
    assert frame.sequence == 7
    assert frame.payload == b"pcm"


def test_parse_wspk_rejects_corrupt_lengths():
    with pytest.raises(ProtocolError):
        parse_wspk(b"WSPK" + bytes([FRAME_AUDIO, 0]) + struct.pack("<II", 1, 99) + b"short")
