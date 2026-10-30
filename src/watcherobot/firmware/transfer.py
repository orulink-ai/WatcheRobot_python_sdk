"""Anonymous, bounded firmware transfer into a content-addressed verified cache."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit

import httpx
from filelock import FileLock

from .catalog import FirmwareRelease, parse_catalog
from .contract import FirmwareError, version
from .package import FirmwarePackage, inspect_package

Progress = Callable[..., None]


def release_record(release: FirmwareRelease) -> dict[str, object]:
    value = asdict(release)
    value.pop("provider")
    for field in (
        "name",
        "description",
        "author",
        "notes",
        "skus",
        "hardware_revisions",
    ):
        if not value[field]:
            value.pop(field)
    if release.provider == "huggingface":
        value.pop("asset_id")
        value.pop("download_url")
    else:
        value.pop("path")
    return value


def validate_release(release: FirmwareRelease) -> FirmwareRelease:
    return parse_catalog(
        json.dumps(
            {"schema_version": 1, "firmwares": [release_record(release)]}
        ).encode(),
        provider=release.provider,
    )[0]


def verify_identity(package: FirmwarePackage, release: FirmwareRelease) -> None:
    if (
        package.firmware_id != release.firmware_id
        or version(package.version) != version(release.version)
        or package.size != release.size
        or package.sha256 != release.sha256
    ):
        raise FirmwareError("Firmware package does not match reviewed release")


def _allowed(url: str, provider: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        return False
    roots = (
        ("huggingface.co", "hf.co", "xethub.hf.co")
        if provider == "huggingface"
        else ("gitee.com", "giteeusercontent.com")
    )
    return any(host == root or host.endswith("." + root) for root in roots)


def acquire_package(
    release: FirmwareRelease,
    cache_root: Path,
    *,
    client: httpx.Client | None = None,
    progress: Progress | None = None,
    offline: bool = False,
) -> Path:
    release = validate_release(release)
    if release.withdrawn:
        raise FirmwareError("Firmware release has been withdrawn")
    root = Path(cache_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (release.sha256 + ".zip")
    emit = progress or (lambda **_: None)
    with FileLock(str(root / (release.sha256 + ".lock")), timeout=30):
        if destination.is_symlink():
            raise FirmwareError("Firmware cache cannot be a symlink")
        if destination.exists():
            try:
                verify_identity(inspect_package(destination), release)
                emit(
                    stage="cached",
                    downloaded_bytes=release.size,
                    total_bytes=release.size,
                )
                return destination
            except FirmwareError:
                destination.unlink()
        if offline:
            raise FirmwareError("A current firmware catalog is required before downloading a new package")
        url = (
            f"https://huggingface.co/datasets/{release.repo_id}/resolve/{release.commit}/{quote(release.path, safe='/')}"
            if release.provider == "huggingface"
            else release.download_url
        )
        fd, name = tempfile.mkstemp(prefix=".download-", dir=root)
        temporary = Path(name)
        owned = client is None
        connection = client or httpx.Client(
            timeout=60, follow_redirects=False, trust_env=True
        )
        try:
            with os.fdopen(fd, "wb") as target:
                for _ in range(6):
                    if not _allowed(url, release.provider):
                        raise FirmwareError("Untrusted firmware download location")
                    # This client is anonymous, even when publication credentials exist.
                    request = connection.build_request(
                        "GET", url, headers={"Accept-Encoding": "identity"}
                    )
                    request.headers.pop("authorization", None)
                    request.headers.pop("cookie", None)
                    response = connection.send(
                        request, stream=True, follow_redirects=False
                    )
                    with closing(response):
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise FirmwareError("Invalid firmware redirect")
                            url = urljoin(url, location)
                            continue
                        if response.status_code != 200:
                            raise FirmwareError(
                                f"Firmware download failed (HTTP {response.status_code})"
                            )
                        done = 0
                        digest = hashlib.sha256()
                        emit(
                            stage="downloading",
                            downloaded_bytes=0,
                            total_bytes=release.size,
                        )
                        for chunk in response.iter_bytes(64 * 1024):
                            done += len(chunk)
                            if done > release.size:
                                raise FirmwareError(
                                    "Firmware download exceeds reviewed size"
                                )
                            target.write(chunk)
                            digest.update(chunk)
                            emit(
                                stage="downloading",
                                downloaded_bytes=done,
                                total_bytes=release.size,
                            )
                        if done != release.size or digest.hexdigest() != release.sha256:
                            raise FirmwareError(
                                "Firmware download integrity check failed"
                            )
                        break
                else:
                    raise FirmwareError("Too many firmware redirects")
                target.flush()
                os.fsync(target.fileno())
            verify_identity(inspect_package(temporary), release)
            os.replace(temporary, destination)
            emit(
                stage="verified",
                downloaded_bytes=release.size,
                total_bytes=release.size,
            )
            return destination
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if isinstance(exc, FirmwareError):
                raise
            raise FirmwareError(
                "Firmware transfer failed; retry the download"
            ) from None
        finally:
            temporary.unlink(missing_ok=True)
            if owned:
                connection.close()
