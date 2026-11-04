"""GIF import limits and timing must match the device AnimPack reader."""
import base64
import io
import struct

import pytest
from PIL import Image
from watcherobot.runtime.daemon.maintenance import work_assets as assets


def gif_payload(count):
    output = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), (i % 256, 0, 0)) for i in range(count)]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=[100 if i % 2 == 0 else 200 for i in range(count)])
    return output.getvalue()


@pytest.mark.parametrize("count", [121, 191, 215, 255])
def test_long_animation_preserves_all_frames_and_delays(count, monkeypatch):
    monkeypatch.setattr(assets, "_rgb565_swapped", lambda frame: b"\x00\x00")
    payload = assets._animation_payload(gif_payload(count))
    header = struct.unpack_from(assets.ANIMPACK_HEADER, payload)
    assert header[4] == count
    delays = [struct.unpack_from(assets.ANIMPACK_FRAME, payload, header[8] + i * 12)[2]
              for i in range(count)]
    assert delays == [100 if i % 2 == 0 else 200 for i in range(count)]


def test_rejects_256_frames_before_pixel_conversion(monkeypatch):
    def unexpected(frame):
        pytest.fail("Over-limit GIF must be rejected before fitting pixels")
    monkeypatch.setattr(assets, "_fit_frame", unexpected)
    with pytest.raises(assets.WorkAssetError, match="255"):
        assets._animation_payload(gif_payload(256))


def test_source_above_old_limit_imports_without_losing_source():
    source = gif_payload(2) + b"\x00" * (3 * 1024 * 1024)
    converted = assets.convert_creator_asset({
        "id": "custom-expression-long", "kind": "expression",
        "fileName": "long.gif", "mimeType": "image/gif",
        "dataUrl": "data:image/gif;base64," + base64.b64encode(source).decode("ascii"),
    })
    assert converted.source_payload == source


def test_rejects_source_above_eight_mib():
    encoded = "data:image/gif;base64," + base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode("ascii")
    with pytest.raises(assets.WorkAssetError, match="8 MB"):
        assets._decode_data_url(encoded, "image/gif", max_bytes=assets.MAX_SOURCE_BYTES_BY_KIND["expression"])


def test_encoded_size_limit_still_applies(monkeypatch):
    monkeypatch.setattr(assets, "MAX_DEVICE_ASSET_BYTES", 100)
    with pytest.raises(assets.WorkAssetError, match="16 MB"):
        assets._animation_payload(gif_payload(2))
