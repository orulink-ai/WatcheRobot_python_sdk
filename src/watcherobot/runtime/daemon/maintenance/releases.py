"""Resolve and download official maintenance packages from GitHub Releases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


GITHUB_API = "https://api.github.com"
SD_TOS_BASE = "https://erroright.tos-cn-guangzhou.volces.com/WatcherRobot/sd"
REPOSITORIES = {
    "firmware": "orulink-ai/WatcheRobot_esp32",
    "sd_resources": "orulink-ai/WatcheRobot_sd",
}
SEMVER_PATTERN = re.compile(r"^[vV]\d+\.\d+\.\d+$")
FIRMWARE_ASSET_PATTERN = re.compile(r"esp32s3\.zip$", re.IGNORECASE)
SD_ASSET_PATTERN = re.compile(r"^watche-sd-resources-[vV]\d+\.\d+\.\d+\.tar\.gz$")
Progress = Callable[[str, int, str], None]


class ReleaseError(RuntimeError):
    """An official release cannot be listed, downloaded, or verified."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    download_url: str

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size}


@dataclass(frozen=True)
class Release:
    version: str
    name: str
    published_at: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "published_at": self.published_at,
            "prerelease": self.prerelease,
            "assets": [asset.payload() for asset in self.assets],
        }


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "WatcheRobot-Desktop-Maintenance/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _request_bytes(url: str, timeout: int = 30) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise ReleaseError(f"下载官方资源失败：{exc}") from exc


def _asset_is_supported(kind: str, name: str) -> bool:
    if kind == "firmware":
        return bool(FIRMWARE_ASSET_PATTERN.search(name)) and "release.zip" not in name.lower()
    if kind == "sd_resources":
        return bool(SD_ASSET_PATTERN.fullmatch(name))
    return False


def _parse_release(kind: str, document: Any) -> Release | None:
    if not isinstance(document, dict) or document.get("draft") is True:
        return None
    version = document.get("tag_name")
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        return None
    assets: list[ReleaseAsset] = []
    for item in document.get("assets", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        size = item.get("size")
        url = item.get("browser_download_url")
        if (
            isinstance(name, str)
            and _asset_is_supported(kind, name)
            and isinstance(size, int)
            and size > 0
            and isinstance(url, str)
            and url.startswith("https://")
        ):
            assets.append(ReleaseAsset(name, size, url))
    if not assets:
        return None
    return Release(
        version=version,
        name=str(document.get("name") or version),
        published_at=str(document.get("published_at") or ""),
        prerelease=bool(document.get("prerelease")),
        assets=tuple(assets),
    )


def list_releases(kind: str, *, limit: int = 20) -> list[dict[str, Any]]:
    repository = REPOSITORIES.get(kind)
    if repository is None:
        raise ReleaseError(f"不支持的发布资源类型：{kind}")
    url = f"{GITHUB_API}/repos/{repository}/releases?per_page={max(1, min(limit, 50))}"
    try:
        documents = json.loads(_request_bytes(url).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"GitHub Release 响应无效：{exc}") from exc
    if not isinstance(documents, list):
        raise ReleaseError("GitHub Release 响应格式无效。")
    return [release.payload() for item in documents if (release := _parse_release(kind, item))]


def _release_document(kind: str, version: str) -> dict[str, Any]:
    repository = REPOSITORIES.get(kind)
    if repository is None or not SEMVER_PATTERN.fullmatch(version):
        raise ReleaseError("选择的 Release 版本无效。")
    url = f"{GITHUB_API}/repos/{repository}/releases/tags/{version}"
    try:
        document = json.loads(_request_bytes(url).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"GitHub Release 响应无效：{exc}") from exc
    if not isinstance(document, dict) or document.get("draft") is True:
        raise ReleaseError(f"找不到可用的官方 Release：{version}")
    return document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _firmware_checksum(document: dict[str, Any], asset_name: str) -> str | None:
    checksum_asset = next(
        (
            item
            for item in document.get("assets", [])
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "sha256sums.txt"
        ),
        None,
    )
    if not checksum_asset:
        return None
    url = checksum_asset.get("browser_download_url")
    if not isinstance(url, str):
        return None
    text = _request_bytes(url).decode("utf-8-sig")
    for line in text.splitlines():
        parts = line.strip().replace(" *", "  ").split()
        if len(parts) >= 2 and parts[1].lstrip("*") == asset_name and re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            return parts[0].lower()
    raise ReleaseError(f"SHA256SUMS.txt 中没有找到 {asset_name}。")


def _sd_manifest(document: dict[str, Any], version: str, asset_name: str) -> tuple[int, str]:
    manifest_asset = next(
        (
            item
            for item in document.get("assets", [])
            if isinstance(item, dict) and item.get("name") == "ota-manifest.json"
        ),
        None,
    )
    if not manifest_asset or not isinstance(manifest_asset.get("browser_download_url"), str):
        raise ReleaseError("SD Release 缺少 ota-manifest.json。")
    try:
        manifest = json.loads(_request_bytes(manifest_asset["browser_download_url"]).decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"SD OTA manifest 无效：{exc}") from exc
    archive = manifest.get("archive") if isinstance(manifest, dict) else None
    if (
        not isinstance(archive, dict)
        or manifest.get("version") != version
        or archive.get("name") != asset_name
        or not isinstance(archive.get("size"), int)
        or not re.fullmatch(r"[a-f0-9]{64}", str(archive.get("sha256", "")).lower())
    ):
        raise ReleaseError("SD OTA manifest 与所选版本或附件不一致。")
    return int(archive["size"]), str(archive["sha256"]).lower()


def _download(
    url: str,
    destination: Path,
    expected_size: int,
    expected_sha256: str | None,
    progress: Progress,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(_request(url), timeout=30) as response, temporary.open("wb") as output:
            while chunk := response.read(256 * 1024):
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                percent = min(100, received * 100 // max(1, expected_size))
                progress("downloading", 2 + percent * 12 // 100, f"正在下载官方安装包：{percent}%")
        if received != expected_size:
            raise ReleaseError(f"下载大小不一致：预期 {expected_size} 字节，实际 {received} 字节。")
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ReleaseError("下载文件 SHA-256 校验失败。")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def acquire_release_package(
    kind: str,
    version: str,
    asset_name: str,
    progress: Progress,
) -> Path:
    """Download a selected official asset and return a verified cached path."""

    document = _release_document(kind, version)
    asset = next(
        (
            item
            for item in document.get("assets", [])
            if isinstance(item, dict) and item.get("name") == asset_name
        ),
        None,
    )
    if asset is None or not _asset_is_supported(kind, asset_name):
        raise ReleaseError("选择的 Release 附件不是可烧录的官方安装包。")
    if version.lower() not in asset_name.lower():
        raise ReleaseError("Release 版本号与所选安装包名称不一致。")
    url = asset.get("browser_download_url")
    size = asset.get("size")
    if not isinstance(url, str) or not isinstance(size, int) or size <= 0:
        raise ReleaseError("Release 附件信息不完整。")

    expected_size = size
    if kind == "firmware":
        expected_sha256 = _firmware_checksum(document, asset_name)
    else:
        expected_size, expected_sha256 = _sd_manifest(document, version, asset_name)
        if expected_size != size:
            raise ReleaseError("GitHub 附件大小与 SD OTA manifest 不一致。")

    cache_root = Path(tempfile.gettempdir()) / "watcher-maintenance-cache" / kind / version
    destination = cache_root / asset_name
    if destination.is_file() and destination.stat().st_size == expected_size:
        if expected_sha256 is None or _sha256(destination) == expected_sha256:
            progress("downloading", 14, f"使用已校验的缓存安装包：{asset_name}")
            return destination
        destination.unlink(missing_ok=True)

    progress("downloading", 2, f"正在从 GitHub 下载 {version}：{asset_name}")
    try:
        _download(url, destination, expected_size, expected_sha256, progress)
    except (OSError, urllib.error.URLError, ReleaseError) as github_error:
        if kind != "sd_resources":
            raise ReleaseError(f"GitHub 固件下载失败：{github_error}") from github_error
        progress("downloading", 2, "GitHub 下载失败，正在切换到官方 TOS 镜像。")
        manifest_url = f"{SD_TOS_BASE}/{version}/ota-manifest.json"
        try:
            manifest = json.loads(_request_bytes(manifest_url).decode("utf-8-sig"))
            archive = manifest["archive"]
            if manifest.get("version") != version or archive.get("name") != asset_name:
                raise ReleaseError("TOS manifest 与所选 SD 版本不一致。")
            tos_size = int(archive["size"])
            tos_sha256 = str(archive["sha256"]).lower()
            if tos_size != expected_size or tos_sha256 != expected_sha256:
                raise ReleaseError("TOS 与 GitHub 发布清单不一致。")
            _download(f"{SD_TOS_BASE}/{version}/{asset_name}", destination, tos_size, tos_sha256, progress)
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError, ReleaseError) as tos_error:
            raise ReleaseError(f"GitHub 与 TOS 均下载失败：{github_error}；{tos_error}") from tos_error
    return destination
