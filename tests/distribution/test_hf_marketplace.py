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

    result = client.read_repository_file(
        repo_id=SPACE_ID,
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

    revision = client.download_repository_snapshot(
        repo_id=SPACE_ID,
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
        client.download_repository_snapshot(
            repo_id=SPACE_ID,
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
        client.download_repository_snapshot(
            repo_id=SPACE_ID,
            commit=COMMIT,
            target=target,
        )


def test_floating_space_revision_is_rejected_before_network() -> None:
    api = FakePublicHfApi()
    client, factory = _client(api)

    with pytest.raises(HubInvalidResponse, match="full commit"):
        client.read_repository_file(
            repo_id=SPACE_ID,
            commit="main",
            path="app.json",
        )

    assert factory.calls == 0
    assert api.calls == []


def test_missing_public_space_has_specific_error() -> None:
    client, _factory = _client(FakePublicHfApi(repo_exists_value=False))

    with pytest.raises(HubRepositoryNotFound):
        client.read_repository_file(
            repo_id=SPACE_ID,
            commit=COMMIT,
            path="app.json",
        )


def test_missing_fixed_commit_has_specific_error() -> None:
    api = FakePublicHfApi(failure=("repo_info", _http_error(404)))
    client, _factory = _client(api)

    with pytest.raises(HubRevisionNotFound):
        client.read_repository_file(
            repo_id=SPACE_ID,
            commit=COMMIT,
            path="app.json",
        )


def test_missing_fixed_file_has_specific_error() -> None:
    api = FakePublicHfApi(failure=("hf_hub_download", _http_error(404)))
    client, _factory = _client(api)

    with pytest.raises(HubFileNotFound):
        client.read_repository_file(
            repo_id=SPACE_ID,
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


def test_snapshot_byte_callback_uses_fixed_total_and_explicit_retry(tmp_path):
    progress = []

    class Api(FakePublicHfApi):
        def snapshot_download(self, **kwargs):
            if kwargs.get("dry_run"):
                return [SimpleNamespace(file_size=10, will_download=True),
                        SimpleNamespace(file_size=30, will_download=True)]
            cls = kwargs["tqdm_class"]
            with cls(total=0, unit="B", name="huggingface_hub.snapshot_download", disable=True) as bar:
                bar.total = 10  # HF discovers individual file totals lazily.
                bar.update(5)
                bar.total = 40
                bar.update(5)
                bar.update(-10)  # HTTP server rejected resume: explicit new attempt.
                bar.update(40)
            with cls(total=40, unit="B", name="huggingface_hub.snapshot_download.transfer") as bar:
                bar.update(40)  # Do not double-count network and reconstruction bars.
            return str(kwargs["local_dir"])

    client, _ = _client(Api())
    client.download_repository_snapshot_with_progress(
        repo_id=SPACE_ID, commit=COMMIT, target=tmp_path,
        on_progress=lambda *values: progress.append(values),
    )
    assert progress == [(0, 40, 0), (5, 40, 0), (0, 40, 1), (40, 40, 1)]


def test_byte_hook_is_called_by_real_hf_progress_factory_when_disabled():
    from huggingface_hub.utils.tqdm import _create_progress_bar
    from watcherobot.distribution.byte_progress import snapshot_progress_bar
    updates = []
    cls = snapshot_progress_bar(None, lambda *values: updates.append(values))
    with _create_progress_bar(cls=cls, log_level=50,
            name="huggingface_hub.snapshot_download", total=0, unit="B") as bar:
        bar.update(7)
    assert updates == [(7, None, 0)]


@pytest.mark.parametrize("mode", ["fresh", "cached", "resume", "retry"])
def test_real_hf_downloader_emits_chunk_bytes_with_mock_http(tmp_path, monkeypatch, mode):
    import hashlib
    import huggingface_hub
    from huggingface_hub import constants
    from huggingface_hub.utils import _http

    files = {"a.bin": b"123456", "b.bin": b"abcdefghij"}
    requests = []
    failed_once = False

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"12"
            raise httpx.ReadTimeout("interrupted fixture")

    def transport(request):
        nonlocal failed_once
        requests.append((request.method, request.url.path))
        path = request.url.path
        if "/tree/" in path:
            return httpx.Response(200, json=[{"type": "file", "path": name,
                "size": len(data), "oid": hashlib.sha1(data).hexdigest()} for name, data in files.items()])
        if path.startswith("/api/spaces/"):
            return httpx.Response(200, json={"id": SPACE_ID, "sha": COMMIT, "private": False,
                "siblings": [{"rfilename": name} for name in files]})
        if "/resolve/" in path:
            data = files[path.rsplit("/", 1)[-1]]
            headers = {"X-Repo-Commit": COMMIT, "ETag": hashlib.sha1(data).hexdigest(),
                       "Content-Length": str(len(data))}
            if request.method == "GET" and path.endswith("a.bin"):
                if mode == "retry" and not failed_once:
                    failed_once = True
                    return httpx.Response(200, headers=headers, stream=InterruptedStream())
                if mode == "resume" and "range" in request.headers:
                    offset = int(request.headers["range"].split("=")[1].split("-")[0])
                    headers["Content-Range"] = f"bytes {offset}-{len(data)-1}/{len(data)}"
                    headers["Content-Length"] = str(len(data) - offset)
                    return httpx.Response(206, headers=headers, content=data[offset:])
            return httpx.Response(200, headers=headers, content=b"" if request.method == "HEAD" else data)
        raise AssertionError((request.method, path))

    previous_factory = _http._GLOBAL_CLIENT_FACTORY
    huggingface_hub.set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(constants, "DOWNLOAD_CHUNK_SIZE", 2)
    from huggingface_hub import file_download
    monkeypatch.setattr(file_download.time, "sleep", lambda _: None)
    target = tmp_path / "snapshot"
    target.mkdir()
    if mode == "cached":
        cached = tmp_path / "cache" / ("spaces--" + SPACE_ID.replace("/", "--")) / "snapshots" / COMMIT / "a.bin"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(files["a.bin"])

    class Api(huggingface_hub.HfApi):
        def snapshot_download(self, *args, **kwargs):
            if mode == "resume" and not kwargs.get("dry_run"):
                from huggingface_hub._local_folder import get_local_download_paths
                paths = get_local_download_paths(local_dir=target, filename="a.bin")
                paths.incomplete_path(hashlib.sha1(files["a.bin"]).hexdigest()).write_bytes(b"12")
            return super().snapshot_download(*args, **kwargs)

    progress = []
    try:
        client = HuggingFaceMarketplaceHubClient(api_factory=lambda: Api(token=False))
        client.download_repository_snapshot_with_progress(repo_id=SPACE_ID, commit=COMMIT,
            target=target, on_progress=lambda *values: progress.append(values))
    finally:
        huggingface_hub.set_client_factory(previous_factory)
    assert {name: (target / name).read_bytes() for name in files} == files
    total = 10 if mode == "cached" else 16
    attempt = 1 if mode == "retry" else 0
    assert progress[0] == (0, total, 0)
    assert progress[-1] == (total, total, attempt)
    assert all(denominator == total for _, denominator, _ in progress)
    assert any(0 < downloaded < 6 for downloaded, _, _ in progress)
    for generation in range(attempt + 1):
        counts = [count for count, _, value in progress if value == generation]
        assert counts == sorted(counts)
    expected_gets = 1 if mode == "cached" else 3 if mode == "retry" else 2
    assert sum(method == "GET" and "/resolve/" in path for method, path in requests) == expected_gets


def test_hf_dependency_floor_matches_the_verified_byte_callback_contract():
    from packaging.requirements import Requirement
    project = Path(__file__).parents[2] / "pyproject.toml"
    dependency = next(line.strip().strip('",') for line in project.read_text().splitlines()
                      if '"huggingface-hub' in line)
    requirement = Requirement(dependency)
    assert "1.32.0" in requirement.specifier
    assert "1.31.0" not in requirement.specifier


def test_byte_callback_throttles_chunks_but_never_delays_retry_or_completion(monkeypatch):
    from watcherobot.distribution import byte_progress
    clock = [0.0]
    monkeypatch.setattr(byte_progress, "monotonic", lambda: clock[0], raising=False)
    updates = []
    cls = byte_progress.snapshot_progress_bar(10000, lambda *value: updates.append(value))
    with cls(unit="B", name="huggingface_hub.snapshot_download") as bar:
        for _ in range(1000):
            bar.update(1)
        assert updates == [(1, 10000, 0)]
        clock[0] = 0.2
        bar.update(1)
        assert updates[-1] == (1001, 10000, 0)
        bar.update(-1001)
        assert updates[-1] == (0, 10000, 1)
        bar.update(10000)
        assert updates[-1] == (10000, 10000, 1)
