from __future__ import annotations

import json
from pathlib import Path

from watcherobot.protocol import build_wspk, parse_wspk


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"


def load_vector(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_target_pairing_fixture_excludes_legacy_sdk_gateway_profile() -> None:
    vectors = load_vector("watcher_lan_pairing_v1.json")

    assert vectors["profile"] == "watcher_lan_pairing_v1"
    assert vectors["protocol"] == "watcher-lan-pairing"
    assert vectors["version"] == "1.0"
    assert vectors["ports"] == {
        "pairing_udp": 37021,
        "device_websocket": 8765,
        "control_rest": 8767,
    }

    serialized = json.dumps(vectors, sort_keys=True)
    assert "SDK_DISCOVER" not in serialized
    assert "watcher-sdk" not in serialized
    assert "sdk_control" not in serialized


def test_wspk_vectors_match_current_sdk_codec() -> None:
    vectors = load_vector("wspk_v1.json")["vectors"]
    current = vectors["current_audio_first"]

    packet = build_wspk(
        current["frame_type"],
        current["flags"],
        current["stream_id"],
        current["sequence"],
        current["payload_utf8"].encode(),
    )

    assert packet.hex() == current["packet_hex"]
    assert parse_wspk(packet).payload == b"pcm"

    legacy = vectors["legacy_audio_last"]
    parsed_legacy = parse_wspk(bytes.fromhex(legacy["packet_hex"]))
    assert parsed_legacy.stream_id == 0
    assert parsed_legacy.sequence == 9
    assert parsed_legacy.payload == b"old"
