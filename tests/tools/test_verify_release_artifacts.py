from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_module():
    path = ROOT / "tools" / "verify_release_artifacts.py"
    spec = importlib.util.spec_from_file_location("verify_release_artifacts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_release(root: Path, *, extra: bool = False) -> dict[str, str]:
    dist = root / "dist"
    dist.mkdir()
    files = {
        "watcherobot-0.1.1-py3-none-any.whl": b"wheel-content",
        "watcherobot-0.1.1.tar.gz": b"sdist-content",
    }
    if extra:
        files["watcherobot-9.9.9-py3-none-any.whl"] = b"unexpected"
    hashes: dict[str, str] = {}
    lines = []
    for name, content in files.items():
        (dist / name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        hashes[name] = digest
        if name != "watcherobot-9.9.9-py3-none-any.whl":
            lines.append(f"{digest}  dist/{name}\n")
    (root / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    return hashes


def test_verified_release_requires_exact_wheel_and_sdist_set(tmp_path: Path) -> None:
    module = _load_module()
    hashes = _write_release(tmp_path)

    manifest = module.verify_release_artifacts(tmp_path, "0.1.1")

    assert [path.name for path in manifest.files] == sorted(hashes)
    assert manifest.hashes == hashes


def test_extra_distribution_not_covered_by_manifest_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    _write_release(tmp_path, extra=True)

    with pytest.raises(ValueError, match="exactly match SHA256SUMS"):
        module.verify_release_artifacts(tmp_path, "0.1.1")


@pytest.mark.parametrize("missing_suffix", [".whl", ".tar.gz"])
def test_missing_required_distribution_type_is_rejected(
    tmp_path: Path, missing_suffix: str
) -> None:
    module = _load_module()
    _write_release(tmp_path)
    removed = next(path for path in (tmp_path / "dist").iterdir() if path.name.endswith(missing_suffix))
    removed.unlink()
    remaining = [
        line
        for line in (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if not line.endswith(removed.name)
    ]
    (tmp_path / "SHA256SUMS").write_text("\n".join(remaining) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="one wheel and one sdist"):
        module.verify_release_artifacts(tmp_path, "0.1.1")


def test_distribution_for_another_version_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    _write_release(tmp_path)

    with pytest.raises(ValueError, match="does not match watcherobot 0.1.2"):
        module.verify_release_artifacts(tmp_path, "0.1.2")


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    _write_release(tmp_path)
    wheel = next((tmp_path / "dist").glob("*.whl"))
    wheel.write_bytes(b"modified")

    with pytest.raises(ValueError, match="checksum mismatch"):
        module.verify_release_artifacts(tmp_path, "0.1.1")


@pytest.mark.parametrize("registry_name", ["TestPyPI", "PyPI"])
def test_registry_must_contain_the_exact_same_distributions(
    tmp_path: Path, registry_name: str
) -> None:
    module = _load_module()
    hashes = _write_release(tmp_path)
    manifest = module.verify_release_artifacts(tmp_path, "0.1.1")
    matching = {
        "urls": [
            {"filename": filename, "digests": {"sha256": digest}}
            for filename, digest in hashes.items()
        ]
    }

    module.verify_registry_artifacts(matching, manifest, registry_name)

    matching["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="do not match immutable release artifacts"):
        module.verify_registry_artifacts(matching, manifest, registry_name)


def test_registry_duplicate_filename_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    hashes = _write_release(tmp_path)
    manifest = module.verify_release_artifacts(tmp_path, "0.1.1")
    filename, digest = next(iter(hashes.items()))
    duplicate = {
        "urls": [
            {"filename": filename, "digests": {"sha256": digest}},
            {"filename": filename, "digests": {"sha256": digest}},
        ]
    }

    with pytest.raises(ValueError, match="duplicate file"):
        module.verify_registry_artifacts(duplicate, manifest, "PyPI")
