"""Prepare signed OTA metadata bound to an actual SDK wheel; never upload it."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "src"))
    from tools.verify_sdk_management_release import verify_wheel
    from watcherobot.management.publishing import main as prepare_catalog
    from watcherobot.management.releases import MAX_CATALOG_BYTES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-wheel", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument(
        "--version", required=True, help="Independent metadata distribution version"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    sdk = verify_wheel(args.sdk_wheel, require_update_trust=True)
    if args.catalog.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError("SDK release catalog exceeds limit")
    payload = json.loads(args.catalog.read_bytes())
    matching = [
        release
        for release in payload["releases"]
        if release["version"] == sdk["sdk_version"]
    ]
    if not matching:
        raise ValueError("Catalog does not describe the SDK wheel version")
    for release in matching:
        artifact = [
            item for item in release["requirements"] if item["name"] == "watcherobot"
        ]
        if len(artifact) != 1 or any(
            (
                artifact[0]["filename"] != args.sdk_wheel.name,
                artifact[0]["sha256"] != sdk["sha256"],
                artifact[0]["size"] != args.sdk_wheel.stat().st_size,
            )
        ):
            raise ValueError(
                "Catalog SDK wheel differs from the actual immutable release"
            )
    with ZipFile(args.sdk_wheel) as archive:
        embedded_keys = archive.read("watcherobot/management/release-keys.json")
    with tempfile.TemporaryDirectory(prefix="watcher-sdk-release-public-") as directory:
        trusted = Path(directory) / "keys.json"
        trusted.write_bytes(embedded_keys)
        # Publisher authentication must match the keys shipped in this SDK,
        # rather than an unrelated caller-supplied trust list.
        return prepare_catalog(
            [
                "--catalog",
                str(args.catalog),
                "--signing-key",
                str(args.signing_key),
                "--trusted-keys",
                str(trusted),
                "--key-id",
                args.key_id,
                "--version",
                args.version,
                "--output",
                str(args.output),
            ]
        )


if __name__ == "__main__":
    raise SystemExit(main())
