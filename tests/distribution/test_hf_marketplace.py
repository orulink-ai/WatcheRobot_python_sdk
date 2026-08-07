from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError

from watcherobot.distribution import hf_marketplace
from watcherobot.distribution.hf_marketplace import (
    HuggingFaceMarketplaceHubClient,
)
from watcherobot.distribution.ports import (
    CatalogDocument,
    HubFileNotFound,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryNotFound,
    HubRevisionNotFound,
)


CATALOG_REPO = "Orulink/watcherobot-app-store"
SPACE_ID = "alice/WatcherRobot-com.example.demo"
COMMIT = "a" * 40


@dataclass
class FakePublicHfApi:
    repo_exists_value: bool = True
    repo_sha: str = COMMIT
    downloaded_path: Path | None = None
    snapshot_return_path: Path | None = None
    materialize_snapshot: bool = False
    failure: tuple[str, Exception] | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def _record(self, name: str, kwargs: dict[str, object]) -> None:
        self.calls.append((name, kwargs))
        if self.failure is not None and self.failure[0] == name:
            raise self.failure[1]

    def repo_exists(self, **kwargs):
        self._record("repo_exists", kwargs)
        return self.repo_exists_value

    def repo_info(self, **kwargs):
        self._record("repo_info", kwargs)
        return SimpleNamespace(sha=self.repo_sha)

    def hf_hub_download(self, **kwargs):
        self._record("hf_hub_download", kwargs)
        assert self.downloaded_path is not None
        return str(self.downloaded_path)

    def snapshot_download(self, **kwargs):
        self._record("snapshot_download", kwargs)
        local_dir = Path(kwargs["local_dir"])
        if self.materialize_snapshot:
            local_dir.joinpath("app.py").write_text(
                "print('snapshot')\n",
                encoding="utf-8",
            )
            metadata = local_dir / ".cache" / "huggingface" / "download"
            metadata.mkdir(parents=True)
            metadata.joinpath("app.py.metadata").write_text(
                "transport metadata",
                encoding="utf-8",
            )
        return str(self.snapshot_return_path or local_dir)


@dataclass
class FakePublicApiFactory:
    api: FakePublicHfApi
    calls: int = 0

    def __call__(self):
        self.calls += 1
        return self.api


def _client(
    api: FakePublicHfApi,
) -> tuple[HuggingFaceMarketplaceHubClient, FakePublicApiFactory]:
    factory = FakePublicApiFactory(api)
    return HuggingFaceMarketplaceHubClient(api_factory=factory), factory


def _http_error(status: int) -> HfHubHTTPError:
    request = httpx.Request("GET", "https://huggingface.co/api/repo")
    response = httpx.Response(status, request=request)
    return HfHubHTTPError("sensitive transport details", response=response)


def test_default_public_api_explicitly_disables_local_token(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_api(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(hf_marketplace, "HfApi", fake_api)

    assert hf_marketplace._default_api_factory() is sentinel
    assert captured["token"] is False


def test_public_catalog_is_pinned_to_observed_dataset_commit(
    tmp_path: Path,
) -> None:
    downloaded = tmp_path / "app-list.json"
    downloaded.write_bytes(b"[]\n")
    api = FakePublicHfApi(downloaded_path=downloaded)
    client, factory = _client(api)

    result = client.read_public_catalog(
        repo_id=CATALOG_REPO,
        path="app-list.json",
    )

    assert result == CatalogDocument(content=b"[]\n", commit=COMMIT)
    assert factory.calls == 1
    assert api.calls == [
        (
            "repo_info",
            {
                "repo_id": CATALOG_REPO,
                "repo_type": "dataset",
                "revision": "main",
            },
        ),
        (
            "hf_hub_download",
            {
                "repo_id": CATALOG_REPO,
                "repo_type": "dataset",
                "filename": "app-list.json",
                "revision": COMMIT,
            },
        ),
    ]


def test_public_space_file_is_read_only_at_the_exact_commit(
    tmp_path: Path,
) -> None:
    downloaded = tmp_path / "app.json"
    downloaded.write_bytes(b'{"schema_version":1}')
    api = FakePublicHfApi(downloaded_path=downloaded)
    client, _factory = _client(api)

    result = client.read_space_file(
        space_id=SPACE_ID,
        commit=COMMIT,
        path="app.json",
    )

    assert result == b'{"schema_version":1}'
    assert api.calls == [
        (
            "repo_exists",
            {"repo_id": SPACE_ID, "repo_type": "space"},
        ),
        (
            "repo_info",
            {
                "repo_id": SPACE_ID,
                "repo_type": "space",
                "revision": COMMIT,
            },
        ),
        (
            "hf_hub_download",
            {
                "repo_id": SPACE_ID,
                "repo_type": "space",
                "filename": "app.json",
                "revision": COMMIT,
            },
        ),
    ]


def test_fixed_snapshot_downloads_to_isolation_and_removes_hub_metadata(
    tmp_path: Path,
) -> None:
    target = tmp_path / "isolated"
    target.mkdir()
    api = FakePublicHfApi(materialize_snapshot=True)
    client, _factory = _client(api)

    revision = client.download_space_snapshot(
        space_id=SPACE_ID,
        commit=COMMIT,
        target=target,
    )

    assert revision.commit == COMMIT
    assert revision.url == (
        f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
    )
    assert target.joinpath("app.py").is_file()
    assert not target.joinpath(".cache", "huggingface").exists()
    assert api.calls[-1] == (
        "snapshot_download",
        {
            "repo_id": SPACE_ID,
            "repo_type": "space",
            "revision": COMMIT,
            "local_dir": target,
        },
    )


def test_hub_local_metadata_removal_retries_transient_windows_directory_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "snapshot"
    metadata = destination / ".cache" / "huggingface"
    metadata.mkdir(parents=True)
    metadata.joinpath("transport.json").write_text("{}", encoding="utf-8")
    original_rmtree = hf_marketplace.shutil.rmtree
    attempts = 0
    delays: list[float] = []

    def transient_rmtree(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(145, "directory is not empty")
        original_rmtree(path)

    monkeypatch.setattr(hf_marketplace.shutil, "rmtree", transient_rmtree)
    monkeypatch.setattr(hf_marketplace.time, "sleep", delays.append)

    hf_marketplace._remove_hub_local_metadata(destination)

    assert attempts == 2
    assert delays == [0.1]
    assert not metadata.exists()


def test_hub_local_metadata_removal_does_not_block_snapshot_after_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "snapshot"
    metadata = destination / ".cache" / "huggingface"
    metadata.mkdir(parents=True)
    metadata.joinpath("transport.json").write_text("{}", encoding="utf-8")
    attempts = 0
    delays: list[float] = []

    def blocked_rmtree(_path: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(145, "directory is not empty")

    monkeypatch.setattr(hf_marketplace.shutil, "rmtree", blocked_rmtree)
    monkeypatch.setattr(hf_marketplace.time, "sleep", delays.append)

    hf_marketplace._remove_hub_local_metadata(destination)

    assert attempts == 5
    assert delays == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert metadata.is_dir()


def test_snapshot_download_rejects_non_empty_adapter_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "isolated"
    target.mkdir()
    target.joinpath("keep.txt").write_text("keep", encoding="utf-8")
    api = FakePublicHfApi()
    client, factory = _client(api)

    with pytest.raises(HubInvalidResponse, match="empty"):
        client.download_space_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
        )

    assert factory.calls == 0


def test_snapshot_download_rejects_unexpected_return_path(
    tmp_path: Path,
) -> None:
    target = tmp_path / "isolated"
    target.mkdir()
    api = FakePublicHfApi(snapshot_return_path=tmp_path / "other")
    client, _factory = _client(api)

    with pytest.raises(HubInvalidResponse, match="target directory"):
        client.download_space_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
        )


def test_floating_space_revision_is_rejected_before_network() -> None:
    api = FakePublicHfApi()
    client, factory = _client(api)

    with pytest.raises(HubInvalidResponse, match="full commit"):
        client.read_space_file(
            space_id=SPACE_ID,
            commit="main",
            path="app.json",
        )

    assert factory.calls == 0
    assert api.calls == []


def test_missing_public_space_has_specific_error() -> None:
    client, _factory = _client(FakePublicHfApi(repo_exists_value=False))

    with pytest.raises(HubRepositoryNotFound):
        client.read_space_file(
            space_id=SPACE_ID,
            commit=COMMIT,
            path="app.json",
        )


def test_missing_fixed_commit_has_specific_error() -> None:
    api = FakePublicHfApi(failure=("repo_info", _http_error(404)))
    client, _factory = _client(api)

    with pytest.raises(HubRevisionNotFound):
        client.read_space_file(
            space_id=SPACE_ID,
            commit=COMMIT,
            path="app.json",
        )


def test_missing_fixed_file_has_specific_error() -> None:
    api = FakePublicHfApi(failure=("hf_hub_download", _http_error(404)))
    client, _factory = _client(api)

    with pytest.raises(HubFileNotFound):
        client.read_space_file(
            space_id=SPACE_ID,
            commit=COMMIT,
            path="app.json",
        )


def test_invalid_or_floating_returned_sha_is_rejected(tmp_path: Path) -> None:
    downloaded = tmp_path / "app-list.json"
    downloaded.write_bytes(b"[]\n")
    client, _factory = _client(
        FakePublicHfApi(repo_sha="main", downloaded_path=downloaded)
    )

    with pytest.raises(HubInvalidResponse, match="full commit"):
        client.read_public_catalog(repo_id=CATALOG_REPO, path="app-list.json")


def test_public_transport_error_is_sanitized() -> None:
    api = FakePublicHfApi(failure=("repo_info", _http_error(503)))
    client, _factory = _client(api)

    with pytest.raises(HubNetworkError) as captured:
        client.read_public_catalog(repo_id=CATALOG_REPO, path="app-list.json")

    assert "sensitive transport details" not in str(captured.value)
