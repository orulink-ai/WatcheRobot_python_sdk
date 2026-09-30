from pathlib import Path

from watcherobot.runtime.identity import source_build_id


def test_frozen_identity_reuses_publication_verification(tmp_path, monkeypatch):
    import sys
    import pytest
    from watcherobot.runtime import identity, repository

    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"runtime")
    instance = tmp_path / "instance"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(instance))
    published = repository.prepare_bundle(source, instance / "bundles")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(published / "runtime.exe"))
    monkeypatch.setattr(repository, "bundle_digest", lambda _: pytest.fail("identity rehashed verified bytes"))
    identity.runtime_identity.cache_clear()
    try:
        assert identity.runtime_identity()["build_id"] == "bundle:" + published.name
    finally:
        identity.runtime_identity.cache_clear()


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
