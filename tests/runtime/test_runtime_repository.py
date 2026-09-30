import os
from pathlib import Path

import pytest

from watcherobot.runtime.repository import (
    _copy_bundle,
    _published_path,
    _remove_bundle,
    _windows_extended_path,
    bundle_digest,
    prepare_bundle,
    operation_lock,
)
from watcherobot.runtime.daemon.instance import RuntimeAlreadyRunningError


def test_unchanged_publication_does_not_reread_bundle_contents(tmp_path, monkeypatch):
    from watcherobot.runtime import repository

    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"runtime")
    target = prepare_bundle(source, tmp_path / "shared")
    monkeypatch.setattr(repository, "bundle_digest", lambda _: pytest.fail("unchanged contents reread"))
    assert prepare_bundle(source, tmp_path / "shared") == target


@pytest.mark.parametrize("damage_target", [False, True])
def test_same_size_rewrite_with_restored_mtime_invalidates_verification(tmp_path, damage_target):
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"good")
    target = prepare_bundle(source, tmp_path / "shared")
    changed = (target if damage_target else source) / "runtime.exe"
    before = changed.stat()
    changed.write_bytes(b"evil")
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns))
    if damage_target:
        with pytest.raises(ValueError, match="integrity"):
            prepare_bundle(source, tmp_path / "shared")
    else:
        assert prepare_bundle(source, tmp_path / "shared") != target


def test_verification_receipt_damage_falls_back_to_content_hash(tmp_path, monkeypatch):
    from watcherobot.runtime import repository

    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"runtime")
    target = prepare_bundle(source, tmp_path / "shared")
    for receipt in (tmp_path / "shared/.verification").glob("*.json"):
        receipt.write_text("not json", encoding="utf-8")
    original = repository.bundle_digest
    calls = []
    monkeypatch.setattr(repository, "bundle_digest", lambda root: (calls.append(root), original(root))[1])
    assert prepare_bundle(source, tmp_path / "shared") == target
    assert len(calls) == 2


def test_unavailable_change_metadata_never_skips_hashing(tmp_path, monkeypatch):
    from watcherobot.runtime import bundle_verification, repository

    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"good")
    target = prepare_bundle(source, tmp_path / "shared")
    monkeypatch.setattr(bundle_verification, "tree_stamp", lambda root: None)
    original = repository.bundle_digest
    calls = []
    monkeypatch.setattr(repository, "bundle_digest", lambda root: (calls.append(root), original(root))[1])
    assert prepare_bundle(source, tmp_path / "shared") == target
    assert len(calls) == 2


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_windows_metadata_api_rejects_other_platforms(monkeypatch, platform):
    from watcherobot.runtime import bundle_verification

    monkeypatch.setattr(bundle_verification.sys, "platform", platform)
    with pytest.raises(OSError, match="unavailable on this platform"):
        bundle_verification._windows_api.__wrapped__()


def test_change_during_verification_is_not_published(tmp_path, monkeypatch):
    from watcherobot.runtime import repository

    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"good")
    original = repository.bundle_digest

    def changing_digest(root):
        result = original(root)
        (root / "runtime.exe").write_bytes(b"changed")
        return result

    monkeypatch.setattr(repository, "bundle_digest", changing_digest)
    with pytest.raises(ValueError, match="changed during verification"):
        prepare_bundle(source, tmp_path / "shared")


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_publish_with_repository_directory_beyond_max_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"fixture")
    root = tmp_path
    while len(str(root)) < 280:
        root /= "中文长目录" * 4
    published = prepare_bundle(source, root)
    assert (published / "runtime.exe").read_bytes() == b"fixture"
    assert prepare_bundle(source, root) == published


def test_bundle_versions_coexist_and_reuse_verified_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"old")
    root = tmp_path / "shared"
    old = prepare_bundle(source, root)
    assert prepare_bundle(source, root) == old
    (source / "runtime.exe").write_bytes(b"new")
    new = prepare_bundle(source, root)
    assert new != old
    assert (old / "runtime.exe").read_bytes() == b"old"
    assert (new / "runtime.exe").read_bytes() == b"new"


def test_corrupt_published_bundle_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.exe").write_bytes(b"good")
    published = prepare_bundle(source, tmp_path / "shared")
    (published / "runtime.exe").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        prepare_bundle(source, tmp_path / "shared")
    assert (published / "runtime.exe").read_bytes() == b"corrupt"


def test_operation_lock_serializes_launchers(tmp_path: Path) -> None:
    with operation_lock(tmp_path, timeout=0):
        with pytest.raises(RuntimeAlreadyRunningError):
            with operation_lock(tmp_path, timeout=0):
                pytest.fail("concurrent lifecycle operation entered")


def test_internal_symlink_is_preserved_and_external_link_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "binary").write_bytes(b"runtime")
    link = source / "alias"
    try:
        link.symlink_to("binary")
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    published = prepare_bundle(source, tmp_path / "shared")
    assert (published / "alias").is_symlink()
    assert (published / "alias").read_bytes() == b"runtime"
    link.unlink()
    (tmp_path / "outside").write_bytes(b"outside")
    link.symlink_to(Path("..") / "outside")
    with pytest.raises(ValueError, match="inside"):
        prepare_bundle(source, tmp_path / "shared")


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits")
def test_bundle_digest_covers_executable_permission(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    executable = source / "runtime"
    executable.write_bytes(b"runtime")
    executable.chmod(0o644)
    before = bundle_digest(source)
    executable.chmod(0o755)
    assert bundle_digest(source) != before


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX executable bits")
def test_bundle_digest_distinguishes_each_executable_bit(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    executable = source / "runtime"
    executable.write_text("same bytes", encoding="utf-8")
    digests = []
    for mode in (0o654, 0o655, 0o754, 0o755):
        executable.chmod(mode)
        digests.append(bundle_digest(source))
    assert len(set(digests)) == len(digests)


def test_windows_bundle_copy_uses_extended_paths(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    captured = None

    def capture_copytree(*args, **kwargs):
        nonlocal captured
        captured = (args, kwargs)

    monkeypatch.setattr(
        "watcherobot.runtime.repository._running_on_windows", lambda: True
    )
    monkeypatch.setattr("watcherobot.runtime.repository.shutil.copytree", capture_copytree)

    _copy_bundle(source, destination)

    assert captured is not None
    assert captured[0] == (_windows_extended_path(source), _windows_extended_path(destination))
    extended_prefix = chr(92) * 2 + "?" + chr(92)
    assert str(captured[0][0]).startswith(extended_prefix)
    assert str(captured[0][1]).startswith(extended_prefix)
    assert captured[1] == {"symlinks": True}


def test_windows_bundle_cleanup_uses_extended_path(tmp_path: Path, monkeypatch) -> None:
    staging = tmp_path / "staging"
    captured = None

    def capture_rmtree(path):
        nonlocal captured
        captured = path

    monkeypatch.setattr(
        "watcherobot.runtime.repository._running_on_windows", lambda: True
    )
    monkeypatch.setattr("watcherobot.runtime.repository.shutil.rmtree", capture_rmtree)

    _remove_bundle(staging)

    assert captured == _windows_extended_path(staging)


def test_windows_published_path_keeps_long_paths_addressable(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "repository" / "digest"
    monkeypatch.setattr(
        "watcherobot.runtime.repository._running_on_windows", lambda: True
    )

    assert _published_path(target) == Path(_windows_extended_path(target))


def test_bundle_digest_ignores_regenerable_python_bytecode(tmp_path: Path) -> None:
    package = tmp_path / "python" / "Lib" / "example"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    package.joinpath("module.py").write_text("VALUE = 1\n", encoding="utf-8")
    clean_digest = bundle_digest(tmp_path)

    package.joinpath("module.pyc").write_bytes(b"legacy-bytecode")
    package.joinpath("module.pyo").write_bytes(b"optimized-bytecode")
    cache.joinpath("module.cpython-312.pyc").write_bytes(b"bytecode")

    assert bundle_digest(tmp_path) == clean_digest
    cache.joinpath("owned.txt").write_text("unexpected", encoding="utf-8")
    assert bundle_digest(tmp_path) != clean_digest


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits")
def test_published_bundle_rejects_executable_permission_damage(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    executable = source / "runtime"
    executable.write_bytes(b"runtime")
    executable.chmod(0o755)
    published = prepare_bundle(source, tmp_path / "shared")
    (published / "runtime").chmod(0o644)
    with pytest.raises(ValueError, match="integrity"):
        prepare_bundle(source, tmp_path / "shared")
