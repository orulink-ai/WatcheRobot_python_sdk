"""Verify SDK wheel management resources before an immutable release is uploaded."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from email.parser import Parser
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


def verify_wheel(wheel: Path, *, require_update_trust: bool = False) -> dict[str, Any]:
    name, version, _, _ = parse_wheel_filename(wheel.name)
    if canonicalize_name(name) != "watcherobot":
        raise ValueError("Expected a watcherobot SDK wheel")
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("SDK wheel contains duplicate resources")
        required = [
            "management/cli.py",
            "management/stream.py",
            "management/compatibility.py",
            "management/publishing.py",
            "management/release-keys.json",
            "runtime/daemon/__main__.py",
        ]
        for relative in required:
            if "watcherobot/" + relative not in names:
                raise ValueError(f"SDK management resource is missing: {relative}")
        metadata_files = [
            path for path in names if path.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError("SDK wheel metadata is ambiguous")
        metadata = Parser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
        if (
            canonicalize_name(metadata.get("Name", "")) != "watcherobot"
            or Version(metadata.get("Version", "")) != version
        ):
            raise ValueError("SDK wheel version does not match its metadata")
        if not any(
            canonicalize_name(Requirement(value).name) == "cryptography"
            for value in metadata.get_all("Requires-Dist", [])
        ):
            raise ValueError("SDK release is missing the cryptography dependency")
        entry = archive.read("watcherobot/runtime/daemon/__main__.py").decode("utf-8")
        if "--sdk-manager-stream" not in entry or "--sdk-manager" not in entry:
            raise ValueError("SDK management entry flags are missing")
        keys = json.loads(archive.read("watcherobot/management/release-keys.json"))
        if not isinstance(keys, dict):
            raise TypeError("SDK release trust keys must be an object")
        for key_id, encoded in keys.items():
            try:
                if (
                    not isinstance(key_id, str)
                    or not key_id
                    or not isinstance(encoded, str)
                ):
                    raise ValueError("Invalid key identity")
                Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(encoded, validate=True)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("SDK release trust key is invalid") from exc
        if require_update_trust and not keys:
            raise ValueError(
                "SDK release trust keys are unconfigured; configure the SDK public key before stable publication"
            )
    return {
        "sdk_version": str(version),
        "management_entry_ready": True,
        "update_trust_ready": bool(keys),
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, nargs="+", required=True)
    parser.add_argument("--require-update-trust", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            [
                verify_wheel(wheel, require_update_trust=args.require_update_trust)
                for wheel in args.wheel
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
