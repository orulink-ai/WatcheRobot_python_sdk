"""Build signed metadata for PyPI mirroring; this module never uploads releases."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from packaging.version import Version


def sign_catalog(
    payload: dict[str, Any], key_id: str, private_key: Ed25519PrivateKey
) -> bytes:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return json.dumps(
        {
            "key_id": key_id,
            "payload": base64.b64encode(raw).decode(),
            "signature": base64.b64encode(private_key.sign(raw)).decode(),
        },
        sort_keys=True,
    ).encode()


def catalog_wheel(version: str, catalog: bytes) -> bytes:
    version = str(Version(version))
    dist = f"watcherobot_desktop_release-{version}.dist-info"
    entries = {
        "watcherobot_desktop_release/__init__.py": b"",
        "watcherobot_desktop_release/catalog.json": catalog,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: watcherobot-desktop-release\nVersion: {version}\nRequires-Python: >=3.10\nSummary: Signed WatcheRobot release metadata\n\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: watcherobot-sdk-manager\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, content in entries.items():
        writer.writerow(
            [
                name,
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .rstrip(b"=")
                .decode(),
                len(content),
            ]
        )
    writer.writerow([f"{dist}/RECORD", "", ""])
    entries[f"{dist}/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def main(argv: list[str] | None = None) -> int:
    """Prepare a validated, signed catalog wheel; publishing remains explicit."""
    import argparse
    import os
    from pathlib import Path

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from .releases import MAX_CATALOG_BYTES, verify_catalog

    parser = argparse.ArgumentParser(
        description="Prepare SDK release metadata without uploading"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--trusted-keys", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if (
        args.catalog.stat().st_size > MAX_CATALOG_BYTES
        or args.signing_key.stat().st_size > 16384
        or args.trusted_keys.stat().st_size > 65536
    ):
        raise ValueError("Release metadata or key file exceeds limit")
    private_key = load_pem_private_key(args.signing_key.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("SDK catalogs require an Ed25519 signing key")
    version = Version(args.version)
    if version.is_prerelease or version.is_devrelease or version.local:
        raise ValueError("Release catalog versions must be stable public versions")
    blob = sign_catalog(json.loads(args.catalog.read_bytes()), args.key_id, private_key)
    verify_catalog(blob, json.loads(args.trusted_keys.read_bytes()))
    wheel = catalog_wheel(str(version), blob)
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"watcherobot_desktop_release-{version}-py3-none-any.whl"
    # Exclusive creation preserves any previously prepared immutable artifact.
    with output.open("xb") as stream:
        try:
            stream.write(wheel)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
