from pathlib import Path

from watcherobot.runtime.identity import source_build_id


def test_source_identity_tracks_edits_but_not_location_or_bytecode(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        root.mkdir()
        (root / "main.py").write_text("value = 1\n")
    assert source_build_id(left) == source_build_id(right)
    (right / "cache.pyc").write_bytes(b"cache")
    assert source_build_id(left) == source_build_id(right)
    (right / "main.py").write_text("value = 2\n")
    assert source_build_id(left) != source_build_id(right)


def test_source_identity_tracks_packaged_resources_and_normalizes_text(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        root.mkdir()
        (root / "main.py").write_text("value = 1\n")
        (root / "settings.json").write_bytes(b'{"enabled": true}\n')
        (root / "asset.bin").write_bytes(b"runtime-resource")
    (right / "main.py").write_bytes(b"value = 1\r\n")
    (right / "settings.json").write_bytes(b'{"enabled": true}\r\n')
    (right / "__pycache__").mkdir()
    (right / "__pycache__" / "main.cpython.pyc").write_bytes(b"cache")

    assert source_build_id(left) == source_build_id(right)
    (right / "settings.json").write_text('{"enabled": false}\n')
    assert source_build_id(left) != source_build_id(right)
