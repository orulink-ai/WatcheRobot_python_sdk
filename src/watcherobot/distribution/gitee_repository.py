"""Gitee repository adapter: atomic Git publication and fork-only submissions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .gitee_auth import GiteeHubClient
from .gitee_public import GiteePublicRepository, _validate_reference
from .ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
    HubAuthenticationError,
    HubCatalogConflict,
    HubInvalidResponse,
    HubNetworkError,
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
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise HubInvalidResponse("Gitee response exceeds limit")
                return response.status, json.loads(raw)
        except HTTPError as exc:
            # Inspect only a bounded error body; never expose server text or credentials.
            with exc:
                body = exc.read(8192).lower()
            if exc.code == 429 or (exc.code == 403 and b"rate limit exceeded" in body):
                return exc.code, {"rate_limited": True}
            return exc.code, {}
        except (URLError, OSError, ValueError):
            raise HubNetworkError("Gitee API request failed") from None


class GiteeGit:
    """Cross-platform Git subprocesses, with ephemeral HTTP authorization."""

    def run(self, root: Path, *args: str, token: AccessToken | None = None) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        config = [
            ("credential.helper", ""),
            ("http.followRedirects", "false"),
            ("core.hooksPath", os.devnull),
        ]
        if token is not None:
            value = base64.b64encode(("oauth2:" + token.value).encode()).decode()
            config.append(
                ("http.https://gitee.com/.extraHeader", "Authorization: Basic " + value)
            )
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_COUNT=str(len(config)),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
        )
        for i, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                env=env,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise HubNetworkError(
                "Git is unavailable or timed out; install Git and retry"
            ) from None
        if result.returncode:
            raise HubNetworkError(
                "Gitee Git operation failed; check permission, connectivity and concurrent changes"
            )
        return result.stdout.decode("utf-8").strip()


class GiteeRepository:
    def __init__(
        self,
        *,
        api: GiteeApi | None = None,
        git: GiteeGit | None = None,
        public: GiteePublicRepository | None = None,
        identity: GiteeHubClient | None = None,
    ) -> None:
        self.api = api or GiteeApi()
        self.git = git or GiteeGit()
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
            raise HubNetworkError("Gitee 请求频率超限，请稍后重试；不会自动重试或切换凭据")
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
        # Materialize before remote mutation; one non-force push publishes all files.
        prepared = [
            (f.path_in_repo, f.content if f.content is not None else _upload_bytes(f))
            for f in files
        ]
        if len(prepared) > 1000 or sum(len(v) for _, v in prepared) > 100 * 1024 * 1024:
            raise HubInvalidResponse("Application snapshot exceeds limits")
        if any(len(content) > 1024 * 1024 for _, content in prepared):
            raise HubInvalidResponse(
                "Gitee publication currently supports files up to 1 MiB"
            )
        for path, _ in prepared:
            _validate_reference(repo_id, "0" * 40, path)
            if any(part.casefold() == ".git" for part in path.split("/")):
                raise HubInvalidResponse("Git metadata cannot be published")
        with tempfile.TemporaryDirectory(prefix="watcher-gitee-publish-") as tmp:
            root = Path(tmp)
            self.git.run(
                root,
                "clone",
                "--no-checkout",
                "--",
                f"https://gitee.com/{repo_id}.git",
                "repo",
                token=token,
            )
            root /= "repo"
            self.git.run(root, "read-tree", "--empty")
            for path, content in prepared:
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            self.git.run(root, "add", "--all")
            self.git.run(
                root,
                "-c",
                "user.name=WatcherRobot",
                "-c",
                "user.email=sdk@orulink.ai",
                "commit",
                "--allow-empty",
                "-m",
                commit_message,
            )
            self.git.run(root, "push", "origin", "HEAD", token=token)

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
        return self.public.read_file(repo_id=repo_id, commit=commit, path=path)

    def read_public_catalog(self, *, repo_id: str, path: str) -> CatalogDocument:
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
        branch = "watcher-submit-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="watcher-gitee-submit-") as tmp:
            root = Path(tmp)
            self.git.run(
                root,
                "clone",
                "--no-checkout",
                "--",
                f"https://gitee.com/{fork_id}.git",
                "repo",
                token=token,
            )
            root /= "repo"
            self.git.run(
                root,
                "fetch",
                "--",
                f"https://gitee.com/{repo_id}.git",
                parent_commit,
                token=token,
            )
            self.git.run(root, "checkout", "-b", branch, parent_commit)
            destination = root / path
            if destination.is_symlink() or any(
                p.is_symlink() for p in destination.parents
            ):
                raise HubInvalidResponse("Catalog path must not be a symbolic link")
            destination.write_bytes(content)
            self.git.run(root, "add", "--", path)
            self.git.run(
                root,
                "-c",
                "user.name=WatcherRobot",
                "-c",
                "user.email=sdk@orulink.ai",
                "commit",
                "-m",
                title,
            )
            self.git.run(root, "push", "origin", branch, token=token)
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
        tree = self._request("GET", f"repos/{repo_id}/git/trees/{commit}?recursive=1")
        if (
            not isinstance(tree, dict)
            or tree.get("truncated")
            or not isinstance(tree.get("tree"), list)
        ):
            raise HubInvalidResponse("Incomplete Gitee source tree")
        files = []
        seen = set()
        total = 0
        for item in tree["tree"]:
            path = item.get("path")
            _validate_reference(repo_id, commit, path)
            key = path.casefold()
            if key in seen or any(
                p.casefold() == ".git" or p.endswith((".", " "))
                for p in path.split("/")
            ):
                raise HubInvalidResponse("Unsafe or duplicate snapshot path")
            reserved = {
                "con",
                "prn",
                "aux",
                "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10)),
            }
            if any(p.split(".")[0].casefold() in reserved for p in path.split("/")):
                raise HubInvalidResponse(
                    "Snapshot contains a reserved Windows filename"
                )
            seen.add(key)
            if item.get("type") == "tree" and item.get("mode") in ("40000", "040000"):
                continue
            if item.get("type") != "blob" or item.get("mode") not in (
                "100644",
                "100755",
            ):
                raise HubInvalidResponse("Symlinks and submodules are not supported")
            size = item.get("size")
            if type(size) is not int or size < 0:
                raise HubInvalidResponse("Invalid Gitee blob size")
            total += size
            files.append(item)
        if len(files) > 1000 or total > 100 * 1024 * 1024:
            raise HubInvalidResponse("Snapshot exceeds size limits")
        try:
            for item in files:
                data = self.read_repository_file(
                    repo_id=repo_id, commit=commit, path=item["path"]
                )
                if len(data) != item["size"]:
                    raise HubInvalidResponse("Snapshot size mismatch")
                digest = hashlib.sha1(
                    f"blob {len(data)}\0".encode() + data, usedforsecurity=False
                ).hexdigest()
                if digest != item.get("sha"):
                    raise HubInvalidResponse(
                        "Snapshot blob does not match the immutable tree"
                    )
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


def _upload_bytes(file: UploadFile) -> bytes:
    assert file.source_path is not None
    return file.source_path.read_bytes()
