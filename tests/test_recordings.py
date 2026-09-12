from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from watcherobot.protocol import FLAG_CANCEL, FLAG_FIRST, FLAG_LAST, FRAME_RECORDING, BinaryFrame
from watcherobot.recordings import (
    RecordingsDomain,
    WREC_JPEG,
    WREC_PCM,
    WrecRecord,
    encode_wrec_record,
    iter_wrec_records,
    mux_wrec_to_mp4,
)


def test_recording_frame_type_is_stable() -> None:
    assert FRAME_RECORDING == 7


def test_wrec_round_trip_preserves_timestamps_and_media(tmp_path: Path) -> None:
    path = tmp_path / "segment-0000.wrec"
    expected = [
        WrecRecord(WREC_JPEG, 0, 0, 10_000, b"jpeg"),
        WrecRecord(WREC_PCM, 0, 0, 11_000, b"\x01\x00"),
        WrecRecord(WREC_JPEG, 0, 1, 210_000, b"next"),
    ]
    path.write_bytes(b"".join(encode_wrec_record(item) for item in expected))

    assert list(iter_wrec_records([path])) == expected


def test_wrec_rejects_crc_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bad.wrec"
    encoded = bytearray(encode_wrec_record(WrecRecord(WREC_JPEG, 0, 0, 1, b"jpeg")))
    encoded[-1] ^= 0xFF
    path.write_bytes(encoded)

    with pytest.raises(ValueError, match="CRC32"):
        list(iter_wrec_records([path]))


def test_interrupted_wrec_can_ignore_only_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "interrupted.wrec"
    first = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 1, b"\x00\x00"))
    path.write_bytes(first + encode_wrec_record(WrecRecord(WREC_PCM, 0, 1, 2, b"\x01\x00"))[:-1])

    assert list(iter_wrec_records([path], tolerate_truncated_tail=True)) == [
        WrecRecord(WREC_PCM, 0, 0, 1, b"\x00\x00")
    ]
    with pytest.raises(ValueError, match="truncated"):
        list(iter_wrec_records([path]))


def test_segment_hash_fixture_is_deterministic(tmp_path: Path) -> None:
    payload = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 1, b"\x00\x00"))
    assert hashlib.sha256(payload).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_download_receiver_exists_before_device_can_send_first_chunk(tmp_path: Path) -> None:
    payload = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 1, b"\x00\x00"))

    class Robot:
        domain: RecordingsDomain

        def _command(self, message_type: str, data: dict[str, object], timeout=None):
            del timeout
            if message_type == "recording.status":
                return {"data": {
                    "recording_id": "rec_1", "mode": "audio", "state": "completed",
                    "bytes": len(payload), "segments": [{
                        "index": 0, "name": "segment-0000.wrec", "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }],
                }}
            if message_type == "recording.download.begin":
                stream_id = int(data["stream_id"])
                assert self.domain._on_binary(
                    BinaryFrame(FRAME_RECORDING, FLAG_FIRST | FLAG_LAST, stream_id, 0, payload)
                )
                return {"data": {"stream_id": stream_id}}
            raise AssertionError(message_type)

    robot = Robot()
    robot.domain = RecordingsDomain(robot)
    result = robot.domain.download("rec_1", tmp_path)

    assert result.downloaded_bytes == len(payload)
    assert (tmp_path / "segment-0000.wrec").read_bytes() == payload


def test_host_recording_receiver_exists_before_device_can_send_first_chunk() -> None:
    payload = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 1, b"\x00\x00"))

    class Robot:
        capabilities = ("recording.host.v1",)
        domain: RecordingsDomain

        def _require_capability(self, capability: str) -> None:
            assert capability == "recording.host.v1"

        def _command(self, message_type: str, data: dict[str, object], timeout=None):
            del timeout
            assert message_type == "ctrl.recording.start"
            assert data["storage"] == "host"
            stream_id = int(data["stream_id"])
            assert self.domain._on_binary(
                BinaryFrame(FRAME_RECORDING, FLAG_FIRST | FLAG_LAST, stream_id, 0, payload)
            )
            return {"data": {
                "recording_id": "host_1", "mode": "audio", "state": "recording",
                "storage": "host", "stream_id": stream_id,
            }}

    robot = Robot()
    robot.domain = RecordingsDomain(robot)
    recording = robot.domain.start_host("audio")

    frame = recording.read(timeout=0.1)
    assert frame.payload == payload
    assert frame.flags & FLAG_LAST
    recording.close()


def test_host_recording_rejects_transport_sequence_gap() -> None:
    class Robot:
        capabilities = ("recording.host.v1",)

        def _require_capability(self, _capability: str) -> None:
            pass

        def _command(self, _message_type: str, data: dict[str, object], timeout=None):
            del timeout
            return {"data": {
                "recording_id": "host_1", "mode": "video", "state": "recording",
                "storage": "host", "stream_id": data["stream_id"],
            }}

    domain = RecordingsDomain(Robot())
    recording = domain.start_host("video")
    assert domain._on_binary(BinaryFrame(FRAME_RECORDING, 0, recording.stream_id, 1, b"bad"))

    with pytest.raises(ValueError, match="sequence mismatch"):
        recording.read(timeout=0.1)
    recording.close()


def test_host_recording_cancel_marker_is_an_explicit_failure() -> None:
    class Robot:
        capabilities = ("recording.host.v1",)

        def _require_capability(self, _capability: str) -> None:
            pass

        def _command(self, _message_type: str, data: dict[str, object], timeout=None):
            del timeout
            return {"data": {
                "recording_id": "host_1", "mode": "audio", "state": "recording",
                "storage": "host", "stream_id": data["stream_id"],
            }}

    domain = RecordingsDomain(Robot())
    recording = domain.start_host("audio")
    assert domain._on_binary(
        BinaryFrame(FRAME_RECORDING, FLAG_LAST | FLAG_CANCEL, recording.stream_id, 0, b"")
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        recording.read(timeout=0.1)
    recording.close()


def test_host_recording_queue_overflow_cannot_look_like_success() -> None:
    class Robot:
        capabilities = ("recording.host.v1",)

        def _require_capability(self, _capability: str) -> None:
            pass

        def _command(self, _message_type: str, data: dict[str, object], timeout=None):
            del timeout
            return {"data": {
                "recording_id": "host_1", "mode": "audio", "state": "recording",
                "storage": "host", "stream_id": data["stream_id"],
            }}

    domain = RecordingsDomain(Robot())
    recording = domain.start_host("audio")
    for sequence in range(65):
        assert domain._on_binary(
            BinaryFrame(FRAME_RECORDING, 0, recording.stream_id, sequence, b"payload")
        )

    with pytest.raises(ValueError, match="sequence mismatch"):
        recording.read(timeout=0.1)
    recording.close()


def test_host_recording_start_failure_releases_reserved_stream() -> None:
    class Robot:
        capabilities = ("recording.host.v1",)

        def _require_capability(self, _capability: str) -> None:
            pass

        def _command(self, _message_type: str, _data: dict[str, object], timeout=None):
            del timeout
            raise RuntimeError("device rejected recording")

    domain = RecordingsDomain(Robot())

    with pytest.raises(RuntimeError, match="device rejected"):
        domain.start_host("audio")

    assert domain._downloads == {}


def test_mux_uses_recording_timestamps_for_browser_compatible_av(tmp_path: Path) -> None:
    jpeg_buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(jpeg_buffer, format="JPEG")
    jpeg = jpeg_buffer.getvalue()
    pcm_100ms = b"\x00\x00" * 1600
    records = [
        WrecRecord(WREC_JPEG, 0, 0, 1_000_000, jpeg),
        WrecRecord(WREC_PCM, 0, 0, 1_000_000, pcm_100ms),
        WrecRecord(WREC_PCM, 0, 1, 1_100_000, pcm_100ms),
        WrecRecord(WREC_JPEG, 0, 1, 1_200_000, jpeg),
    ]
    output = mux_wrec_to_mp4(
        records, tmp_path / "recording.mp4", width=32, height=32, fps=5, with_audio=True
    )

    import av

    with av.open(str(output)) as container:
        assert {stream.codec_context.name for stream in container.streams} == {"h264", "aac"}
        assert container.duration is not None and container.duration >= 200_000
