from __future__ import annotations

import hashlib
import hmac
import zlib

import pytest

from watcherobot.runtime.daemon.preview.udp_protocol import (
    FaceTrackingUdpProtocolError,
    FaceTrackingUdpReassembler,
    build_preview_bundle,
    encode_preview_datagrams,
    parse_preview_bundle,
)


SESSION_TOKEN = "ab" * 32
SESSION_KEY = hmac.new(
    SESSION_TOKEN.encode("ascii"), b"face-preview-v1", hashlib.sha256
).digest()


def make_bundle(sequence: int = 7, jpeg_size: int = 2600) -> bytes:
    telemetry = (
        '{"v":1,"kind":"frame","seq":%d,"size":[416,416],'
        '"error":[0,0],"velocity":[0,0]}' % sequence
    )
    image = b"FTW1" + bytes(20) + bytes((index % 251 for index in range(jpeg_size)))
    return build_preview_bundle(telemetry, image)


def test_udp_reassembler_accepts_out_of_order_fragments_and_duplicates() -> None:
    bundle = make_bundle()
    packets = encode_preview_datagrams(
        bundle,
        session_key=SESSION_KEY,
        stream_id=11,
        sequence=7,
        max_datagram_size=1200,
    )
    receiver = FaceTrackingUdpReassembler(session_key=SESSION_KEY, clock=lambda: 1.0)

    assert receiver.push(packets[1]) is None
    assert receiver.push(packets[1]) is None
    assert receiver.push(packets[0]) is None
    completed = None
    for packet in reversed(packets[2:]):
        completed = receiver.push(packet) or completed

    assert completed is not None
    assert completed.stream_id == 11
    assert completed.sequence == 7
    assert completed.bundle == bundle
    telemetry, image = parse_preview_bundle(completed.bundle)
    assert '"seq":7' in telemetry
    assert image.startswith(b"FTW1")


def test_newer_sequence_discards_incomplete_old_frame_and_old_packets() -> None:
    old_packets = encode_preview_datagrams(
        make_bundle(7), session_key=SESSION_KEY, stream_id=11, sequence=7
    )
    new_packets = encode_preview_datagrams(
        make_bundle(8), session_key=SESSION_KEY, stream_id=11, sequence=8
    )
    receiver = FaceTrackingUdpReassembler(session_key=SESSION_KEY, clock=lambda: 1.0)

    assert receiver.push(old_packets[0]) is None
    for packet in new_packets:
        completed = receiver.push(packet)
    assert completed is not None and completed.sequence == 8
    assert receiver.push(old_packets[-1]) is None
    assert receiver.stats.superseded_frames == 1
    assert receiver.stats.stale_datagrams == 1


def test_timeout_and_crc_failure_never_publish_partial_frame() -> None:
    now = [1.0]
    packets = encode_preview_datagrams(
        make_bundle(), session_key=SESSION_KEY, stream_id=11, sequence=7
    )
    receiver = FaceTrackingUdpReassembler(
        session_key=SESSION_KEY, clock=lambda: now[0], frame_timeout_seconds=0.25
    )
    assert receiver.push(packets[0]) is None
    now[0] = 1.3
    assert receiver.push(packets[1]) is None
    assert receiver.stats.timed_out_frames == 1

    corrupt = []
    for packet in packets:
        changed = bytearray(packet)
        changed[24] ^= 0xFF
        changed[32:48] = hmac.new(
            SESSION_KEY,
            bytes(changed[:32]) + bytes(changed[48:]),
            hashlib.sha256,
        ).digest()[:16]
        corrupt.append(bytes(changed))
    receiver = FaceTrackingUdpReassembler(session_key=SESSION_KEY, clock=lambda: 2.0)
    assert all(receiver.push(packet) is None for packet in corrupt)
    assert receiver.stats.crc_failures == 1


def test_protocol_rejects_invalid_limits_and_bundle_shape() -> None:
    with pytest.raises(FaceTrackingUdpProtocolError):
        encode_preview_datagrams(
            make_bundle(),
            session_key=b"short",
            stream_id=1,
            sequence=1,
        )
    with pytest.raises(FaceTrackingUdpProtocolError):
        encode_preview_datagrams(
            make_bundle(),
            session_key=SESSION_KEY,
            stream_id=1,
            sequence=1,
            max_datagram_size=40,
        )
    with pytest.raises(FaceTrackingUdpProtocolError):
        parse_preview_bundle(b"\xff\xffbad")
    assert zlib.crc32(make_bundle()) >= 0
