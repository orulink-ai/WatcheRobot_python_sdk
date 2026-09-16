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

    monkeypatch.setattr("watcherobot.runtime.repository.os.name", "nt")
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

    monkeypatch.setattr("watcherobot.runtime.repository.os.name", "nt")
    monkeypatch.setattr("watcherobot.runtime.repository.shutil.rmtree", capture_rmtree)

    _remove_bundle(staging)

    assert captured == _windows_extended_path(staging)


def test_windows_published_path_keeps_long_paths_addressable(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "repository" / "digest"
    monkeypatch.setattr("watcherobot.runtime.repository.os.name", "nt")

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
