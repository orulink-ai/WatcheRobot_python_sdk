from __future__ import annotations

import pytest

from watcherobot.runtime.daemon.preview.udp_feedback import (
    FaceTrackingUdpFeedbackError,
    decode_preview_ack,
    encode_preview_ack,
)


SESSION_KEY = bytes(range(32))


def test_ack_round_trip_authenticates_stream_and_sequence() -> None:
    packet = encode_preview_ack(
        session_key=SESSION_KEY,
        stream_id=0x12345678,
        sequence=0xFFFFFFFE,
    )

    ack = decode_preview_ack(packet, session_key=SESSION_KEY)

    assert len(packet) == 32
    assert ack.stream_id == 0x12345678
    assert ack.sequence == 0xFFFFFFFE


def test_ack_rejects_tampering_wrong_key_and_unknown_flags() -> None:
    packet = bytearray(
        encode_preview_ack(session_key=SESSION_KEY, stream_id=7, sequence=9)
    )
    packet[12] ^= 1
    with pytest.raises(FaceTrackingUdpFeedbackError, match="authentication"):
        decode_preview_ack(bytes(packet), session_key=SESSION_KEY)

    valid = encode_preview_ack(session_key=SESSION_KEY, stream_id=7, sequence=9)
    with pytest.raises(FaceTrackingUdpFeedbackError, match="authentication"):
        decode_preview_ack(valid, session_key=b"x" * 32)

    packet = bytearray(valid)
    packet[5] = 1
    with pytest.raises(FaceTrackingUdpFeedbackError, match="header"):
        decode_preview_ack(bytes(packet), session_key=SESSION_KEY)
