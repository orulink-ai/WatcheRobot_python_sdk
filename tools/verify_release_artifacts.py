"""Fail closed unless release files exactly match their manifest and registry copy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import urllib.request

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version


_SUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *](dist/[^/]+)$")


@dataclass(frozen=True)
class VerifiedManifest:
    files: tuple[Path, ...]
    hashes: dict[str, str]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(root: Path) -> dict[str, str]:
    sums_path = root / "SHA256SUMS"
    lines = sums_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("SHA256SUMS must not be empty")
    hashes: dict[str, str] = {}
    for line in lines:
        match = _SUM_LINE.fullmatch(line)
        if match is None:
            raise ValueError("SHA256SUMS contains an invalid distribution path")
        relative = PurePosixPath(match.group(2))
        filename = relative.name
        if filename in hashes:
            raise ValueError(f"SHA256SUMS contains duplicate file {filename}")
        hashes[filename] = match.group(1)
    return hashes


def _validate_filename(filename: str, version: Version) -> str:
    if filename.endswith(".whl"):
        name, parsed_version, _build, _tags = parse_wheel_filename(filename)
        kind = "wheel"
    elif filename.endswith(".tar.gz"):
        name, parsed_version = parse_sdist_filename(filename)
        kind = "sdist"
    else:
        raise ValueError(f"unsupported release distribution {filename}")
    if canonicalize_name(name) != "watcherobot" or parsed_version != version:
        raise ValueError(
            f"distribution {filename} does not match watcherobot {version}"
        )
    return kind


def verify_release_artifacts(root: Path, expected_version: str) -> VerifiedManifest:
    version = Version(expected_version)
    hashes = _read_manifest(root)
    dist = root / "dist"
    if not dist.is_dir():
        raise ValueError("release dist directory is missing")
    actual = {path.name: path for path in dist.iterdir() if path.is_file()}
    if set(actual) != set(hashes):
        raise ValueError("release files must exactly match SHA256SUMS")

    kinds = [_validate_filename(filename, version) for filename in sorted(actual)]
    if kinds.count("wheel") != 1 or kinds.count("sdist") != 1 or len(kinds) != 2:
        raise ValueError("release must contain exactly one wheel and one sdist")

    for filename, expected_hash in hashes.items():
        actual_hash = _sha256_file(actual[filename])
        if actual_hash != expected_hash:
            raise ValueError(f"checksum mismatch for {filename}")
    return VerifiedManifest(
        files=tuple(actual[name] for name in sorted(actual)),
        hashes=hashes,
    )


def verify_registry_artifacts(
    payload: dict[str, object], manifest: VerifiedManifest, registry_name: str
) -> None:
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise ValueError(f"{registry_name} returned invalid release metadata")
    registry_hashes: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict):
            raise ValueError(f"{registry_name} returned invalid release metadata")
        filename = item.get("filename")
        digests = item.get("digests")
        if not isinstance(filename, str) or not isinstance(digests, dict):
            raise ValueError(f"{registry_name} returned invalid release metadata")
        digest = digests.get("sha256")
        if not isinstance(digest, str):
            raise ValueError(f"{registry_name} did not provide SHA-256 digests")
        if filename in registry_hashes:
            raise ValueError(f"{registry_name} returned duplicate file {filename}")
        registry_hashes[filename] = digest
    if registry_hashes != manifest.hashes:
        raise ValueError(
            f"{registry_name} files do not match immutable release artifacts"
        )


def _load_registry(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "watcherobot-release-artifact-verifier/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("registry returned invalid release metadata")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--registry-json-url")
    parser.add_argument("--registry-name", default="registry")
    parser.add_argument("--print-paths", action="store_true")
    args = parser.parse_args()

    manifest = verify_release_artifacts(args.root, args.version)
    if args.registry_json_url:
        verify_registry_artifacts(
            _load_registry(args.registry_json_url), manifest, args.registry_name
        )
    if args.print_paths:
        for path in manifest.files:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
