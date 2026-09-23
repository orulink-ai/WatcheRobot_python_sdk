"""Gitee OpenAPI repository adapter for reads, publication, and submissions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .gitee_auth import GiteeHubClient
from .download import MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_FILES
from .gitee_public import GiteePublicRepository, _validate_reference
from .ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
    HubAuthenticationError,
    HubCatalogConflict,
    HubFileNotFound,
    HubInvalidResponse,
    HubNetworkError,
    HubRateLimitError,
    HubRepositoryConflict,
    RepositoryRevision,
    SourceRepository,
    UploadFile,
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class GiteeApi:
    """Bounded JSON transport; credentials never enter URLs or error messages."""

    def request(
        self,
        method: str,
        path: str,
        token: AccessToken | None,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token.value
        request = Request(
            "https://gitee.com/api/v5/" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                limit = 2 * MAX_SNAPSHOT_BYTES + 1024 * 1024
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise HubInvalidResponse("Gitee response exceeds limit")
                return response.status, json.loads(raw)
        except HTTPError as exc:
            # Inspect only a bounded error body; never expose server text or credentials.
            try:
                with exc:
                    body = exc.read(8192).lower()
            except (OSError, ValueError):
                raise HubNetworkError("Gitee API request failed") from None
            if exc.code == 429 or (exc.code == 403 and b"rate limit exceeded" in body):
                return exc.code, {"rate_limited": True}
            return exc.code, {}
        except (URLError, OSError, ValueError):
            raise HubNetworkError("Gitee API request failed") from None


class GiteeRepository:
    def __init__(
        self,
        *,
        api: GiteeApi | None = None,
        public: GiteePublicRepository | None = None,
        identity: GiteeHubClient | None = None,
    ) -> None:
        self.api = api or GiteeApi()
        self.public = public or GiteePublicRepository()
        self.identity = identity or GiteeHubClient()

    def _request(
        self,
        method: str,
        path: str,
        token: AccessToken | None = None,
        data: dict[str, Any] | None = None,
        allowed: tuple[int, ...] = (200, 201),
    ) -> Any:
        status, payload = self.api.request(method, path, token, data)
        if status == 429 or (
            status == 403 and isinstance(payload, dict)
            and payload.get("rate_limited") is True
        ):
            raise HubRateLimitError("Gitee 请求频率超限，请稍后重试；不会自动重试或切换凭据")
        if status == 403 and token is None:
            raise HubNetworkError("Gitee 匿名读取被拒绝（HTTP 403），可能涉及限流或仓库访问限制")
        if status in (401, 403):
            raise HubAuthenticationError(
                "Gitee permission denied; check token scope and account security binding"
            )
        if status not in allowed:
            raise HubNetworkError(f"Gitee API operation failed (HTTP {status})")
        return payload

    def ensure_public_repository(
        self, token: AccessToken, *, repo_id: str, sdk: str
    ) -> SourceRepository:
        _validate_reference(repo_id, "0" * 40, "app.json")
        user = self.identity.whoami(token).username
        if repo_id.split("/")[0] != user:
            raise HubRepositoryConflict(
                "Publish only to the authenticated developer repository"
            )
        status, repo = self.api.request("GET", f"repos/{repo_id}", token)
        created = status == 404
        if created:
            repo = self._request(
                "POST",
                "user/repos",
                token,
                {"name": repo_id.split("/")[1], "auto_init": True, "private": False},
            )
            repo = self._request(
                "PATCH",
                f"repos/{repo_id}",
                token,
                {"name": repo_id.split("/")[1], "private": False},
            )
        elif status != 200:
            raise HubRepositoryConflict("Cannot verify Gitee repository ownership")
        if not isinstance(repo, dict) or repo.get("private") is not False:
            raise HubRepositoryConflict(
                "Gitee source must be public; existing private repositories are not changed"
            )
        return SourceRepository(repo_id, created)

    def replace_repository_files(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None:
        _validate_reference(repo_id, "0" * 40, "app.json")
        prepared = [
            (f.path_in_repo, f.content if f.content is not None else _upload_bytes(f))
            for f in files
        ]
        if len(prepared) > MAX_SNAPSHOT_FILES or sum(len(v) for _, v in prepared) > MAX_SNAPSHOT_BYTES:
            raise HubInvalidResponse("Application snapshot exceeds limits")
        seen: set[str] = set()
        nodes: dict[str, tuple[str, bool]] = {}
        for path, _ in prepared:
            _validate_snapshot_path(repo_id, "0" * 40, path, seen, nodes)
        revision = self.get_repository_head(token, repo_id=repo_id)
        repo = self._request("GET", f"repos/{repo_id}", token)
        branch = repo.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise HubInvalidResponse("Gitee repository has no default branch")
        remote = self._tree(repo_id, revision.commit, token)
        desired = {path: content for path, content in prepared}
        actions: list[dict[str, Any]] = []
        for path, content in desired.items():
            digest = _blob_digest(content)
            current = remote.get(path)
            if current is not None and current.get("sha") == digest:
                continue
            action = {
                "action": "update" if current is not None else "create",
                "path": path,
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            }
            if current is not None:
                action["last_commit_id"] = self._last_file_commit(
                    token, repo_id=repo_id, commit=revision.commit, path=path,
                )
            actions.append(action)
        for path in sorted(set(remote) - set(desired)):
            actions.append({
                "action": "delete", "path": path,
                "last_commit_id": self._last_file_commit(
                    token, repo_id=repo_id, commit=revision.commit, path=path,
                ),
            })
        if len(actions) > MAX_SNAPSHOT_FILES:
            raise HubInvalidResponse("Application publication requires too many file operations")
        if actions:
            result = self._request(
                "POST", f"repos/{repo_id}/commits", token,
                {"branch": branch, "message": commit_message, "actions": actions},
            )
            commit = result.get("sha") if isinstance(result, dict) else None
            _validate_reference(repo_id, commit, "app.json")
            assert isinstance(commit, str)
        else:
            commit = revision.commit
        actual = self._tree(repo_id, commit, token)
        expected = {path: _blob_digest(content) for path, content in desired.items()}
        if {path: item.get("sha") for path, item in actual.items()} != expected:
            raise HubInvalidResponse("Published Gitee snapshot differs from source files")

    def get_repository_head(
        self, token: AccessToken, *, repo_id: str
    ) -> RepositoryRevision:
        _validate_reference(repo_id, "0" * 40, "app.json")
        repo = self._request("GET", f"repos/{repo_id}", token)
        branch = repo.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise HubInvalidResponse("Gitee repository has no default branch")
        data = self._request(
            "GET", f"repos/{repo_id}/branches/{quote(branch, safe='')}", token
        )
        commit = data.get("commit", {}).get("sha")
        _validate_reference(repo_id, commit, "app.json")
        return RepositoryRevision(commit, f"https://gitee.com/{repo_id}/tree/{commit}")

    def read_repository_file(
        self, token: AccessToken | None = None, *, repo_id: str, commit: str, path: str
    ) -> bytes:
        _validate_reference(repo_id, commit, path)
        blobs = self._tree(repo_id, commit, None)
        return self._read_blob(repo_id, path, blobs)

    def _last_file_commit(
        self, token: AccessToken, *, repo_id: str, commit: str, path: str,
    ) -> str:
        """Resolve the file guard within the already selected immutable revision."""
        _validate_reference(repo_id, commit, path)
        query = urlencode({"sha": commit, "path": path, "per_page": 1})
        history = self._request("GET", f"repos/{repo_id}/commits?{query}", token)
        if not isinstance(history, list) or len(history) != 1 or not isinstance(history[0], dict):
            raise HubInvalidResponse("Gitee returned invalid file commit history")
        file_commit = history[0].get("sha")
        _validate_reference(repo_id, file_commit, path)
        assert isinstance(file_commit, str)
        return file_commit

    def read_public_catalog(self, *, repo_id: str, path: str) -> CatalogDocument:
        _validate_reference(repo_id, '0' * 40, path)
        return self.public.read_public_catalog(repo_id=repo_id, path=path)

    def read_catalog(
        self, token: AccessToken, *, repo_id: str, path: str
    ) -> CatalogDocument:
        return self.read_public_catalog(repo_id=repo_id, path=path)

    def list_open_catalog_pull_requests(
        self, token: AccessToken, *, repo_id: str, author: str
    ) -> tuple[CatalogPullRequest, ...]:
        _validate_reference(repo_id, "0" * 40, "app-list.json")
        result: list[CatalogPullRequest] = []
        for page in range(1, 101):
            query = urlencode(
                dict(state="open", author=author, page=page, per_page=100)
            )
            items = self._request("GET", f"repos/{repo_id}/pulls?{query}", token)
            if not isinstance(items, list):
                raise HubInvalidResponse("Invalid Gitee pull request list")
            result.extend(self._pull(item) for item in items)
            if len(items) < 100:
                return tuple(result)
        raise HubInvalidResponse("Too many pending Gitee pull requests")

    @staticmethod
    def _pull(item: Any) -> CatalogPullRequest:
        try:
            return CatalogPullRequest(
                item["number"], item["title"], item["html_url"], "open"
            )
        except (KeyError, TypeError, ValueError):
            raise HubInvalidResponse("Invalid Gitee pull request") from None

    def create_catalog_pull_request(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
        content: bytes,
        parent_commit: str,
        title: str,
        description: str,
    ) -> CatalogPullRequest:
        _validate_reference(repo_id, parent_commit, path)
        if self.get_repository_head(token, repo_id=repo_id).commit != parent_commit:
            raise HubCatalogConflict("Catalog changed; submit again")
        user = self.identity.whoami(token).username
        fork_id = user + "/" + repo_id.split("/")[1]
        if fork_id == repo_id:
            raise HubRepositoryConflict(
                "Use a developer account distinct from the catalog owner"
            )
        status, fork = self.api.request("GET", f"repos/{fork_id}", token)
        if status == 404:
            fork = self._request("POST", f"repos/{repo_id}/forks", token, {})
        elif status != 200:
            raise HubRepositoryConflict("Cannot access developer fork")
        if (
            not isinstance(fork, dict)
            or fork.get("parent", {}).get("full_name", "").lower() != repo_id.lower()
        ):
            raise HubRepositoryConflict(
                "Existing repository is not a fork of the selected catalog"
            )
        file_commit = self._last_file_commit(
            token, repo_id=repo_id, commit=parent_commit, path=path,
        )
        branch = "watcher-submit-" + uuid.uuid4().hex
        created_branch = self._request(
            "POST", f"repos/{fork_id}/branches", token,
            {"refs": parent_commit, "branch_name": branch},
        )
        start = created_branch.get("commit") if isinstance(created_branch, dict) else None
        if not isinstance(start, dict) or start.get("sha") != parent_commit:
            raise HubInvalidResponse("Gitee submission branch did not start at the selected commit")
        self._request(
            "POST", f"repos/{fork_id}/commits", token,
            {
                "branch": branch,
                "message": title,
                "actions": [{
                    "action": "update",
                    "path": path,
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                    "last_commit_id": file_commit,
                }],
            },
        )
        if self.get_repository_head(token, repo_id=repo_id).commit != parent_commit:
            raise HubCatalogConflict(
                "Catalog changed; fork branch retained, submit again"
            )
        repo = self._request("GET", f"repos/{repo_id}", token)
        item = self._request(
            "POST",
            f"repos/{repo_id}/pulls",
            token,
            {
                "head": f"{fork_id}:{branch}",
                "base": repo["default_branch"],
                "title": title,
                "body": description,
            },
        )
        return self._pull(item)

    def download_repository_snapshot(
        self, *, repo_id: str, commit: str, target: Path
    ) -> RepositoryRevision:
        _validate_reference(repo_id, commit, "app.json")
        if not target.is_dir() or any(target.iterdir()):
            raise HubInvalidResponse("Snapshot target must be empty")
        tree = self._request(
            "GET", f"repos/{repo_id}/git/trees/{commit}?recursive=1", None
        )
        blobs = self._tree(repo_id, commit, None, payload=tree)
        return self._export_snapshot(
            repo_id, commit, target, tree,
            lambda **kwargs: self._read_blob(repo_id, kwargs["path"], blobs),
        )

    def _tree(
        self, repo_id: str, commit: str, token: AccessToken | None,
        *, payload: Any | None = None,
    ) -> dict[str, dict[str, Any]]:
        if payload is None:
            payload = self._request(
                "GET", f"repos/{repo_id}/git/trees/{commit}?recursive=1", token
            )
        if not isinstance(payload, dict) or payload.get("truncated") is True:
            raise HubInvalidResponse("Incomplete Gitee source tree")
        items = payload.get("tree")
        if not isinstance(items, list):
            raise HubInvalidResponse("Invalid Gitee source tree")
        result: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        nodes: dict[str, tuple[str, bool]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise HubInvalidResponse("Invalid Gitee source tree entry")
            path = item.get("path")
            if not isinstance(path, str):
                raise HubInvalidResponse("Invalid Gitee source tree path")
            _validate_snapshot_path(
                repo_id, commit, path, seen, nodes,
                is_directory=item.get("type") == "tree",
            )
            if item.get("type") == "tree":
                if item.get("mode") not in ("40000", "040000"):
                    raise HubInvalidResponse("Invalid Gitee directory mode")
                continue
            if item.get("type") != "blob" or item.get("mode") not in ("100644", "100755"):
                raise HubInvalidResponse("Symlinks and submodules are not supported")
            _validate_reference(repo_id, item.get("sha"), path)
            result[path] = item
        return result

    def _read_blob(
        self, repo_id: str, path: str, blobs: dict[str, dict[str, Any]],
    ) -> bytes:
        item = blobs.get(path)
        if item is None:
            raise HubFileNotFound("Gitee source file was not found")
        size = item.get("size")
        if type(size) is not int or not 0 <= size <= MAX_SNAPSHOT_BYTES:
            raise HubInvalidResponse("Invalid Gitee blob size")
        payload = self._request(
            "GET", f"repos/{repo_id}/git/blobs/{item['sha']}", None
        )
        encoded = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(encoded, str) or payload.get("encoding") != "base64":
            raise HubInvalidResponse("Gitee returned invalid blob content")
        try:
            data = base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error):
            raise HubInvalidResponse("Gitee returned invalid blob encoding") from None
        _verify_blob(item, data)
        return data

    def _export_snapshot(
        self, repo_id: str, commit: str, target: Path, tree: Any, read_file: Any,
    ) -> RepositoryRevision:
        if (
            not isinstance(tree, dict)
            or tree.get("truncated")
            or not isinstance(tree.get("tree"), list)
        ):
            raise HubInvalidResponse("Incomplete Gitee source tree")
        files = []
        seen: set[str] = set()
        nodes: dict[str, tuple[str, bool]] = {}
        total = 0
        for item in tree["tree"]:
            path = item.get("path")
            _validate_snapshot_path(
                repo_id, commit, path, seen, nodes,
                is_directory=item.get("type") == "tree",
            )
            if item.get("type") == "tree" and item.get("mode") in ("40000", "040000"):
                continue
            if item.get("type") != "blob" or item.get("mode") not in (
                "100644",
                "100755",
            ):
                raise HubInvalidResponse("Symlinks and submodules are not supported")
            size = item.get("size")
            if type(size) is not int or not 0 <= size <= MAX_SNAPSHOT_BYTES:
                raise HubInvalidResponse("Invalid Gitee blob size")
            total += size
            files.append(item)
        if len(files) > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
            raise HubInvalidResponse("Snapshot exceeds size limits")
        try:
            for item in files:
                data = read_file(
                    repo_id=repo_id, commit=commit, path=item["path"]
                )
                _verify_blob(item, data)
                destination = target / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                if item["mode"] == "100755":
                    destination.chmod(0o755)
        except Exception:
            for child in target.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            raise
        return RepositoryRevision(commit, f"https://gitee.com/{repo_id}/tree/{commit}")


def _validate_snapshot_path(
    repo_id: str, commit: str, path: str, seen: set[str],
    nodes: dict[str, tuple[str, bool]], *, is_directory: bool = False,
) -> None:
    """Apply identical portable path rules before publication or export."""
    _validate_reference(repo_id, commit, path)
    key = path.casefold()
    parts = path.split("/")
    if key in seen or any(
        part.casefold() == ".git" or part.endswith((".", " "))
        for part in parts
    ):
        raise HubInvalidResponse("Unsafe or duplicate snapshot path")
    reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if any(part.split(".")[0].casefold() in reserved for part in parts):
        raise HubInvalidResponse("Snapshot contains a reserved Windows filename")
    # Publication lists files only; Git export also lists explicit directories.
    # Record implicit parents so both representations enforce the same tree.
    for depth in range(1, len(parts) + 1):
        prefix = "/".join(parts[:depth])
        node = (prefix, depth < len(parts) or is_directory)
        previous = nodes.get(prefix.casefold())
        if previous is not None and previous != node:
            raise HubInvalidResponse("Conflicting snapshot directory spelling or type")
        nodes[prefix.casefold()] = node
    seen.add(key)


def _upload_bytes(file: UploadFile) -> bytes:
    assert file.source_path is not None
    return file.source_path.read_bytes()


def _blob_digest(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode() + content,
        usedforsecurity=False,
    ).hexdigest()


def _verify_blob(item: dict[str, Any], content: bytes) -> None:
    if len(content) != item["size"]:
        raise HubInvalidResponse("Snapshot size mismatch")
    if _blob_digest(content) != item.get("sha"):
        raise HubInvalidResponse("Snapshot blob does not match the immutable tree")
