import os
from pathlib import Path

import pytest

from watcherobot.runtime.repository import bundle_digest, prepare_bundle, operation_lock
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
