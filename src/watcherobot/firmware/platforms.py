"""HF Dataset and Gitee Release adapters. Publication never starts a device job."""

from __future__ import annotations

import base64
import io
import json
import re
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

import httpx
from huggingface_hub import CommitOperationAdd, HfApi

from watcherobot.distribution.gitee_git import GiteeGitRepository
from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.hf_marketplace import HuggingFaceMarketplaceHubClient
from watcherobot.distribution.hf_publish import HuggingFacePublishHubClient
from watcherobot.distribution.ports import AccessToken
from watcherobot.distribution.providers import get_provider

from .catalog import CATALOG_PATH, FirmwareRelease, parse_catalog
from .contract import FirmwareError, version
from .package import inspect_package
from .transfer import Progress, acquire_package, release_record, validate_release


def _commit(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise FirmwareError("Platform returned an invalid immutable revision")
    return value


class _UploadReader(io.BufferedReader):
    def __init__(self, raw: Any, emit: Progress, size: int):
        super().__init__(raw)
        self.emit, self.size = emit, size

    def read(self, size: int | None = -1) -> bytes:
        value = super().read(size)
        self.emit(stage="uploading", processed_bytes=self.tell(), total_bytes=self.size)
        return value


class FirmwarePlatform:
    def __init__(
        self,
        provider: str,
        *,
        hf_factory: Callable[[Any], Any] | None = None,
        client: httpx.Client | None = None,
    ):
        get_provider(provider)
        self.provider = provider
        self.hf_factory = hf_factory or (lambda token: HfApi(token=token))
        self.client = client

    def catalog(self) -> tuple[str, tuple[FirmwareRelease, ...]]:
        reader = (
            HuggingFaceMarketplaceHubClient()
            if self.provider == "huggingface"
            else GiteeGitRepository()
        )
        document = reader.read_public_catalog(
            repo_id=get_provider(self.provider).catalog, path=CATALOG_PATH
        )
        return document.commit, parse_catalog(document.content, provider=self.provider)

    def _gitee(
        self,
        method: str,
        path: str,
        token: AccessToken,
        *,
        allow_missing: bool = False,
        **kwargs: Any,
    ) -> Any:
        own = self.client is None
        client = self.client or httpx.Client(timeout=120, follow_redirects=False)
        try:
            response = client.request(
                method,
                "https://gitee.com/api/v5/" + path,
                headers={"Authorization": "Bearer " + token.value},
                **kwargs,
            )
            if allow_missing and response.status_code == 404:
                return None
            if response.status_code not in (200, 201):
                raise FirmwareError(
                    f"Gitee operation failed (HTTP {response.status_code}); check permissions and retry"
                )
            if len(response.content) > 1024 * 1024:
                raise FirmwareError("Gitee metadata exceeds limit")
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            raise FirmwareError(
                "Gitee operation failed; retry after checking connectivity"
            ) from None
        finally:
            if own:
                client.close()

    def publish(
        self, path: Path, token: AccessToken, *, progress: Progress | None = None
    ) -> FirmwareRelease:
        # Read an immutable local snapshot: editors/builds may replace the selected ZIP during upload.
        from .package import MAX_PACKAGE_BYTES

        with tempfile.TemporaryDirectory(prefix="firmware-publication-") as temporary:
            snapshot = Path(temporary) / "firmware.zip"
            with Path(path).open("rb") as source, snapshot.open("wb") as target:
                total = 0
                while chunk := source.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_PACKAGE_BYTES:
                        raise FirmwareError("Firmware package exceeds size limit")
                    target.write(chunk)
            return self._publish(snapshot, token, progress=progress)

    def _publish(
        self, path: Path, token: AccessToken, *, progress: Progress | None = None
    ) -> FirmwareRelease:
        emit = progress or (lambda **_: None)
        package = inspect_package(path)
        release_version = ".".join(map(str, version(package.version)))
        metadata = json.dumps(asdict(package), ensure_ascii=True).encode()
        emit(stage="preparing_upload", total_bytes=package.size)
        if self.provider == "huggingface":
            api = self.hf_factory(token.value)
            user = api.whoami()["name"]
            if (
                not isinstance(user, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", user) is None
            ):
                raise FirmwareError("Invalid publishing identity")
            repo = f"{user}/WatcherRobot-Firmware-{package.firmware_id}"
            api.create_repo(
                repo_id=repo, repo_type="dataset", private=False, exist_ok=True
            )
            info = api.repo_info(repo_id=repo, repo_type="dataset")
            if getattr(info, "private", False):
                raise FirmwareError("Firmware repository must be public")
            if getattr(info, "sha", None) is None:
                api.create_commit(
                    repo_id=repo,
                    repo_type="dataset",
                    commit_message="Initialize firmware repository",
                    operations=[
                        CommitOperationAdd(
                            path_in_repo="README.md",
                            path_or_fileobj=b"# WatcheRobot firmware\n",
                        )
                    ],
                )
                info = api.repo_info(repo_id=repo, repo_type="dataset")
            head = _commit(info.sha)
            prefix = f"versions/{release_version}/"
            existing_paths = api.get_paths_info(
                repo_id=repo,
                repo_type="dataset",
                paths=[prefix + "firmware.zip"],
                revision=head,
                expand=True,
            )
            if existing_paths:
                version_commit = _commit(
                    getattr(
                        getattr(existing_paths[0], "last_commit", None), "oid", None
                    )
                )
                try:
                    saved = Path(
                        api.hf_hub_download(
                            repo_id=repo,
                            repo_type="dataset",
                            filename=prefix + "release.json",
                            revision=version_commit,
                        )
                    ).read_bytes()
                    if json.loads(saved) != json.loads(metadata):
                        raise FirmwareError(
                            "Firmware version belongs to different content"
                        )
                except (AttributeError, OSError, ValueError):
                    raise FirmwareError(
                        "Firmware version already exists with different or invalid metadata"
                    ) from None
                return validate_release(
                    FirmwareRelease(
                        "huggingface",
                        package.firmware_id,
                        package.version,
                        repo,
                        version_commit,
                        package.size,
                        package.sha256,
                        False,
                        prefix + "firmware.zip",
                        name=package.name,
                        description=package.description,
                        author=package.author,
                        notes=package.notes,
                        skus=package.skus,
                        hardware_revisions=package.hardware_revisions,
                    )
                )
            # HF handles LFS/Xet; report stages rather than fabricate wire bytes from file reads.
            result = api.create_commit(
                repo_id=repo,
                repo_type="dataset",
                parent_commit=head,
                commit_message=f"Publish firmware {package.version}",
                operations=[
                    CommitOperationAdd(
                        path_in_repo=prefix + "firmware.zip", path_or_fileobj=Path(path)
                    ),
                    CommitOperationAdd(
                        path_in_repo=prefix + "release.json", path_or_fileobj=metadata
                    ),
                ],
            )
            release = FirmwareRelease(
                "huggingface",
                package.firmware_id,
                package.version,
                repo,
                _commit(result.oid),
                package.size,
                package.sha256,
                False,
                prefix + "firmware.zip",
            )
        else:
            user = self._gitee("GET", "user", token).get("login")
            if (
                not isinstance(user, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", user) is None
            ):
                raise FirmwareError("Invalid publishing identity")
            repo = f"{user}/WatcherRobot-Firmware-{package.firmware_id}"
            information = self._gitee("GET", f"repos/{repo}", token, allow_missing=True)
            if information is None:
                information = self._gitee(
                    "POST",
                    "user/repos",
                    token,
                    json={
                        "name": repo.split("/")[1],
                        "private": False,
                        "auto_init": True,
                    },
                )
            if (
                information.get("private") is not False
                or information.get("full_name", "").lower() != repo.lower()
            ):
                raise FirmwareError(
                    "Firmware repository must be public and owned by the publishing account"
                )
            tag = "v" + package.version.removeprefix("v")
            existing = self._gitee(
                "GET", f"repos/{repo}/releases/tags/{tag}", token, allow_missing=True
            )
            binding = "WatcherRobot firmware SHA256: " + package.sha256
            if existing is not None:
                if existing.get("body") != binding:
                    raise FirmwareError(
                        "Firmware version already belongs to different content"
                    )
                commit = _commit(existing.get("target_commitish"))
                remote_release = existing
            else:
                meta_path = f"versions/{release_version}/manifest.json"
                existing_meta = self._gitee(
                    "GET",
                    f"repos/{repo}/contents/{meta_path}",
                    token,
                    allow_missing=True,
                )
                if existing_meta is None:
                    meta = self._gitee(
                        "POST",
                        f"repos/{repo}/contents/{meta_path}",
                        token,
                        json={
                            "branch": information["default_branch"],
                            "content": base64.b64encode(metadata).decode(),
                            "message": f"Publish firmware metadata {package.version}",
                        },
                    )
                    commit = _commit(meta["commit"]["sha"])
                else:
                    try:
                        saved = base64.b64decode(existing_meta["content"])
                        if json.loads(saved) != json.loads(metadata):
                            raise FirmwareError(
                                "Firmware version metadata belongs to different content"
                            )
                    except (KeyError, ValueError):
                        raise FirmwareError(
                            "Existing firmware metadata is invalid"
                        ) from None
                    history = self._gitee(
                        "GET",
                        f"repos/{repo}/commits?path={meta_path}&per_page=1",
                        token,
                    )
                    if not isinstance(history, list) or not history:
                        raise FirmwareError(
                            "Cannot resolve the existing firmware metadata commit"
                        )
                    commit = _commit(history[0].get("sha"))
                remote_release = self._gitee(
                    "POST",
                    f"repos/{repo}/releases",
                    token,
                    json={
                        "tag_name": tag,
                        "name": package.name + " " + package.version,
                        "body": binding,
                        "target_commitish": commit,
                        "prerelease": False,
                    },
                )
            release_id = remote_release.get("id")
            if type(release_id) is not int or release_id <= 0:
                raise FirmwareError("Invalid Gitee Release ID")
            asset_path = f"repos/{repo}/releases/{release_id}/attach_files"
            assets = self._gitee("GET", asset_path + "?per_page=100", token)
            if not isinstance(assets, list):
                raise FirmwareError("Invalid Gitee attachment list")
            matching = [item for item in assets if item.get("name") == "firmware.zip"]
            if len(matching) > 1:
                raise FirmwareError("Ambiguous firmware attachment")
            if matching:
                asset = matching[0]
            else:
                with _UploadReader(
                    Path(path).open("rb", buffering=0), emit, package.size
                ) as stream:
                    asset = self._gitee(
                        "POST",
                        asset_path,
                        token,
                        files={"file": ("firmware.zip", stream, "application/zip")},
                    )
            asset_id = asset.get("id")
            if (
                type(asset_id) is not int
                or asset_id <= 0
                or asset.get("size") != package.size
            ):
                raise FirmwareError("Invalid Gitee firmware attachment")
            release = FirmwareRelease(
                "gitee",
                package.firmware_id,
                package.version,
                repo,
                commit,
                package.size,
                package.sha256,
                False,
                asset_id=asset_id,
                download_url=f"https://gitee.com/api/v5/{asset_path}/{asset_id}/download",
            )
        emit(stage="uploaded", total_bytes=package.size)
        return validate_release(
            replace(
                release,
                name=package.name,
                description=package.description,
                author=package.author,
                notes=package.notes,
                skus=package.skus,
                hardware_revisions=package.hardware_revisions,
            )
        )

    def submit(
        self,
        release: FirmwareRelease,
        token: AccessToken,
        *,
        cache_root: Path,
        progress: Progress | None = None,
    ) -> dict[str, object]:
        if release.provider != self.provider:
            raise FirmwareError("Selected provider does not match firmware publication")
        # Always anonymously re-download and inspect before offering the version to users.
        with tempfile.TemporaryDirectory(prefix="firmware-public-check-") as temporary:
            acquire_package(release, Path(temporary), progress=progress)
        repository = (
            HuggingFacePublishHubClient()
            if self.provider == "huggingface"
            else GiteeRepository()
        )
        catalog_repo = get_provider(self.provider).catalog
        document = repository.read_catalog(
            token, repo_id=catalog_repo, path=CATALOG_PATH
        )
        releases = list(parse_catalog(document.content, provider=self.provider))
        for item in releases:
            if item.firmware_id == release.firmware_id and version(
                item.version
            ) == version(release.version):
                if item != release:
                    raise FirmwareError(
                        "Catalog version already has a different immutable reference"
                    )
                return {"status": "already_listed"}
        releases.append(release)
        content = json.dumps(
            {
                "schema_version": 1,
                "firmwares": [release_record(item) for item in releases],
            },
            ensure_ascii=True,
            indent=2,
        ).encode()
        title = f"WatcherRobot firmware: {release.firmware_id}@{release.version} {release.sha256[:12]}"
        identity = (
            self.hf_factory(token.value).whoami()["name"]
            if self.provider == "huggingface"
            else self._gitee("GET", "user", token)["login"]
        )
        for pr in repository.list_open_catalog_pull_requests(
            token, repo_id=catalog_repo, author=identity
        ):
            if pr.title == title:
                return {"status": "pending", "pr_url": pr.url}
        result = repository.create_catalog_pull_request(
            token,
            repo_id=catalog_repo,
            path=CATALOG_PATH,
            parent_commit=document.commit,
            title=title,
            description="Review ESP32 firmware metadata, compatibility and package digest.",
            content=content,
        )
        return {"status": "pending", "pr_url": result.url}
