"""Anonymous Gitee Git HTTPS reads; no OpenAPI, system Git, hooks or credentials."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from dulwich.client import HttpGitClient
from dulwich.config import ConfigDict
from dulwich.errors import GitProtocolError, NotGitRepository
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo
from filelock import FileLock, Timeout
from urllib3 import PoolManager, ProxyManager
from urllib.request import getproxies, proxy_bypass
import certifi

from watcherobot.runtime.windows_paths import filesystem_path

from .download import MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_FILES
from .gitee_public import CATALOG_REPO_ID, CATALOG_PATH, _validate_reference
from .gitee_repository import _validate_snapshot_path
from .ports import (
    CatalogDocument,
    HubError,
    HubFileNotFound,
    HubInvalidResponse,
    HubLocalIOError,
    HubNetworkError,
    HubRevisionNotFound,
    RepositoryRevision,
    SnapshotProgress,
)

_MAX_TRANSFER_BYTES = 256 * 1024 * 1024
_MAX_CACHE_BYTES = 1024 * 1024 * 1024
_FETCH_SECONDS = 180.0
_MAX_FILE_BYTES = 1024 * 1024


def default_git_cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (
            Path(os.environ["LOCALAPPDATA"])
            / "WatcheRobot"
            / "distribution"
            / "gitee-git"
        )
    return Path.home() / ".cache" / "watcherobot" / "gitee-git"


class GitTransport(Protocol):
    def latest(self, repo_id: str) -> str: ...
    def fetch(
        self, repo_id: str, target: Repo, commit: str | None, depth: int
    ) -> None: ...


class _BoundedClient(HttpGitClient):
    def __init__(self, repo_id: str, *, deadline: float, budget: list[int]) -> None:
        self._prefix = f"https://gitee.com/{repo_id}.git/"
        self._deadline = deadline
        self._budget = budget
        super().__init__(
            "https://gitee.com",
            config=ConfigDict(),
            timeout=30,
            pool_manager=(
                ProxyManager(
                    getproxies()["https"], retries=False, ca_certs=certifi.where()
                )
                if getproxies().get("https") and not proxy_bypass("gitee.com")
                else PoolManager(retries=False, ca_certs=certifi.where())
            ),
            username=None,
            password=None,
        )

    def _http_request(
        self,
        url: str,
        headers: Any = None,
        data: Any = None,
        raise_for_status: bool = True,
    ) -> Any:
        if not url.startswith(self._prefix) or time.monotonic() > self._deadline:
            raise HubNetworkError(
                "Gitee Git request exceeded its allowed scope or deadline"
            )
        response, read = super()._http_request(url, headers, data, raise_for_status)

        def bounded_read(size: int = -1) -> bytes:
            if time.monotonic() > self._deadline:
                response.close()
                raise HubNetworkError("Gitee Git download timed out")
            chunk = read(min(size, 65536) if size >= 0 else 65536)
            self._budget[0] += len(chunk)
            if self._budget[0] > _MAX_TRANSFER_BYTES:
                response.close()
                raise HubInvalidResponse("Gitee Git transfer exceeds size limit")
            return chunk

        return response, bounded_read


class GiteeGitTransport:
    """Only HTTPS Git smart protocol; empty config disables credential helpers."""

    def __init__(self) -> None:
        self.deadline = 0.0
        self.budget = [0]

    def latest(self, repo_id: str) -> str:
        self.deadline = time.monotonic() + _FETCH_SECONDS
        self.budget = [0]
        client = _BoundedClient(repo_id, deadline=self.deadline, budget=self.budget)
        try:
            refs = client.get_refs(f"/{repo_id}.git").refs
            sha = refs.get(b"refs/heads/master")
            if sha is None:
                raise HubRevisionNotFound(
                    "Official Gitee catalog master branch was not found"
                )
            return sha.decode("ascii")
        except HubError:
            raise
        except Exception:
            raise HubNetworkError(
                "Unable to read the Gitee catalog Git reference"
            ) from None
        finally:
            client.pool_manager.clear()

    def fetch(self, repo_id: str, target: Repo, commit: str | None, depth: int) -> None:
        # One budget spans direct SHA and bounded history fallback attempts.
        if depth == 1:
            self.deadline = time.monotonic() + _FETCH_SECONDS
            self.budget = [0]
        client = _BoundedClient(repo_id, deadline=self.deadline, budget=self.budget)

        def wants(refs: dict[bytes, bytes], *args: Any, **kwargs: Any) -> list[bytes]:
            if commit is not None:
                return [commit.encode("ascii")]
            return list(
                set(sha for ref, sha in refs.items() if ref.startswith(b"refs/heads/"))
            )

        try:
            client.fetch(
                f"/{repo_id}.git",
                target,
                determine_wants=wants,
                depth=depth,
                protocol_version=0,
                progress=lambda _: None,
            )
        except HubError:
            raise
        except (GitProtocolError, NotGitRepository) as exc:
            # Only a refused object request permits history fallback, never network errors.
            if any(
                text in str(exc).lower()
                for text in ("not our ref", "unadvertised", "not a valid", "not found")
            ):
                raise HubRevisionNotFound(
                    "Reviewed Gitee commit is not directly available"
                ) from None
            raise HubNetworkError("Gitee Git source transfer failed") from None
        except Exception:
            raise HubNetworkError("Gitee Git source transfer failed") from None
        finally:
            client.pool_manager.clear()


class GiteeGitRepository:
    """Immutable per-commit disk cache shared by marketplace and installation."""

    def __init__(
        self, *, cache_root: Path | None = None, transport: GitTransport | None = None
    ) -> None:
        self.cache_root = filesystem_path(
            Path(cache_root) if cache_root is not None else default_git_cache_root()
        )
        self.transport = transport or GiteeGitTransport()

    def _lock(self) -> FileLock:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.cache_root / ".lock"), timeout=240)

    def read_public_catalog(
        self, *, repo_id: str = CATALOG_REPO_ID, path: str = CATALOG_PATH
    ) -> CatalogDocument:
        _validate_reference(repo_id, "0" * 40, path)
        commit = self.transport.latest(repo_id)
        return CatalogDocument(
            self.read_repository_file(repo_id=repo_id, commit=commit, path=path), commit
        )

    def read_repository_file(self, *, repo_id: str, commit: str, path: str) -> bytes:
        _validate_reference(repo_id, commit, path)
        try:
            with self._lock():
                cached = self._ensure(repo_id, commit)
                with Repo(str(cached / "repo")) as repo:
                    files = self._files(repo, repo_id, commit)
                    item = next((entry for entry in files if entry[0] == path), None)
                    if item is None:
                        raise HubFileNotFound("Gitee source file was not found")
                    blob = repo[item[2]]
                    if len(blob.data) > _MAX_FILE_BYTES:
                        raise HubInvalidResponse(
                            "Gitee metadata file exceeds size limit"
                        )
                    return bytes(blob.data)
        except Timeout:
            raise HubNetworkError("Gitee Git cache is busy; retry later") from None
        except OSError as exc:
            raise HubLocalIOError("Unable to read the local Git cache") from exc
        except (KeyError, ValueError):
            raise HubInvalidResponse("Gitee Git cache or source is invalid") from None

    def download_repository_snapshot_with_progress(
        self,
        *,
        repo_id: str,
        commit: str,
        target: Path,
        on_progress: SnapshotProgress,
    ) -> RepositoryRevision:
        return self.download_repository_snapshot(
            repo_id=repo_id, commit=commit, target=target, on_progress=on_progress
        )

    def download_repository_snapshot(
        self,
        *,
        repo_id: str,
        commit: str,
        target: Path,
        on_progress: SnapshotProgress | None = None,
    ) -> RepositoryRevision:
        _validate_reference(repo_id, commit, "app.json")
        target = filesystem_path(target)
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise HubInvalidResponse("Snapshot target must be an empty directory")
        try:
            with self._lock():
                cached = self._ensure(repo_id, commit)
                with Repo(str(cached / "repo")) as repo:
                    files = self._files(repo, repo_id, commit)
                    total = sum(len(repo[oid].data) for _, _, oid in files)
                    done = 0
                    if on_progress:
                        on_progress(0, total, 0)
                    for path, mode, oid in files:
                        dest = target / path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(repo[oid].data)
                        if mode == 0o100755:
                            dest.chmod(0o755)
                        done += len(repo[oid].data)
                        if on_progress:
                            on_progress(done, total, 0)
        except BaseException as exc:
            for child in target.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            if isinstance(exc, Timeout):
                raise HubNetworkError("Gitee Git cache is busy; retry later") from None
            if isinstance(exc, OSError):
                raise HubLocalIOError(
                    "Unable to write local Application source files"
                ) from exc
            if isinstance(exc, (KeyError, ValueError)):
                raise HubInvalidResponse(
                    "Gitee Git cache or source is invalid"
                ) from None
            raise
        return RepositoryRevision(commit, f"https://gitee.com/{repo_id}/tree/{commit}")

    def _ensure(self, repo_id: str, commit: str) -> Path:
        key = hashlib.sha256(repo_id.encode()).hexdigest()[:20] + "-" + commit
        dest = self.cache_root / key
        if dest.is_dir():
            try:
                marker = json.loads((dest / "complete.json").read_text())
                if marker != {"repo_id": repo_id, "commit": commit}:
                    raise ValueError("cache identity mismatch")
                with Repo(str(dest / "repo")) as repo:
                    self._files(repo, repo_id, commit)
                os.utime(dest, None)
                return dest
            except (OSError, ValueError, KeyError, HubError):
                shutil.rmtree(dest)
        # Only this process owns the global lock; abandoned staging is safe to remove.
        for abandoned in self.cache_root.glob(".fetch-*"):
            if abandoned.is_dir():
                shutil.rmtree(abandoned)
        staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=self.cache_root))
        try:
            with Repo.init_bare(str(staging / "repo"), mkdir=True) as repo:
                try:
                    self.transport.fetch(repo_id, repo, commit, 1)
                except HubRevisionNotFound:
                    pass
                for depth in (32, 256):
                    if commit.encode() in repo.object_store:
                        break
                    self.transport.fetch(repo_id, repo, None, depth)
                if commit.encode() not in repo.object_store:
                    raise HubRevisionNotFound(
                        "Reviewed commit unavailable within the bounded Git history"
                    )
                self._files(repo, repo_id, commit)
            (staging / "complete.json").write_text(
                json.dumps({"repo_id": repo_id, "commit": commit})
            )
            staging.rename(dest)
            self._prune(dest)
            return dest
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _prune(self, keep: Path) -> None:
        entries = [
            p
            for p in self.cache_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for entry in [keep] + [p for p in entries if p != keep]:
            total += sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
            if total > _MAX_CACHE_BYTES and entry != keep:
                shutil.rmtree(entry)

    @staticmethod
    def _files(repo: Repo, repo_id: str, commit: str) -> list[tuple[str, int, bytes]]:
        try:
            obj = repo[commit.encode()]
            if not isinstance(obj, Commit) or obj.id != commit.encode():
                raise HubInvalidResponse("Gitee did not return the exact commit object")
            obj.check()
            files: list[tuple[str, int, bytes]] = []
            seen: set[str] = set()
            nodes: dict[str, tuple[str, bool]] = {}
            size = 0

            def walk(oid: bytes, prefix: str = "", depth: int = 0) -> None:
                nonlocal size
                if depth > 32 or len(nodes) > 4000:
                    raise HubInvalidResponse("Gitee source tree exceeds limits")
                tree = repo[oid]
                if not isinstance(tree, Tree) or tree.id != oid:
                    raise HubInvalidResponse("Invalid Gitee Git tree")
                tree.check()
                for name, mode, child in tree.iteritems():
                    path = prefix + name.decode("utf-8")
                    _validate_snapshot_path(
                        repo_id, commit, path, seen, nodes, is_directory=mode == 0o40000
                    )
                    if mode == 0o40000:
                        walk(child, path + "/", depth + 1)
                    elif mode in (0o100644, 0o100755):
                        blob = repo[child]
                        if not isinstance(blob, Blob) or blob.id != child:
                            raise HubInvalidResponse("Invalid Gitee Git blob")
                        blob.check()
                        if blob.data.startswith(
                            b"version https://git-lfs.github.com/spec/v1"
                        ):
                            raise HubInvalidResponse(
                                "Git LFS source files are not supported"
                            )
                        size += len(blob.data)
                        files.append((path, mode, child))
                        if size > MAX_SNAPSHOT_BYTES or len(files) > MAX_SNAPSHOT_FILES:
                            raise HubInvalidResponse(
                                "Gitee source snapshot exceeds size limits"
                            )
                    else:
                        raise HubInvalidResponse(
                            "Symlinks, submodules and special files are not supported"
                        )

            walk(obj.tree)
            return files
        except HubError:
            raise
        except Exception:
            raise HubInvalidResponse(
                "Gitee Git objects are incomplete or invalid"
            ) from None
