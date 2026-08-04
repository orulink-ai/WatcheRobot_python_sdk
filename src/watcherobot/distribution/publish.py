"""Application publishing orchestration independent from Hub implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .check import check_application
from .catalog_submission import (
    CatalogDocumentError,
    CatalogPullRequestConflict,
    catalog_pull_request_title,
    plan_catalog_submission,
)
from .credentials import CredentialStoreError
from .events import ErrorCode, EventSink, ProgressEvent
from .login import LoginError, login_status
from .ports import (
    AccessToken,
    CredentialStore,
    HubAuthenticationError,
    HubCatalogConflict,
    HubClient,
    HubError,
    HubRepositoryConflict,
    PublishHubClient,
)
from .publish_files import prepare_space_upload_files


CATALOG_REPO_ID = "Orulink/watcherobot-app-store"
CATALOG_PATH = "app-list.json"
SPACE_SDK = "static"


class PublishError(RuntimeError):
    """Sanitized publishing failure with a stable machine contract."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True)
class PublishResult:
    """Public fixed-revision and catalog state after publishing."""

    space_id: str
    commit: str
    space_url: str
    source_url: str
    pr_url: str
    pr_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "space_id": self.space_id,
            "commit": self.commit,
            "space_url": self.space_url,
            "source_url": self.source_url,
            "pr_url": self.pr_url,
            "pr_status": self.pr_status,
        }


def publish_application(
    application_dir: Path,
    *,
    credentials: CredentialStore,
    identity_hub: HubClient,
    publish_hub: PublishHubClient,
    events: EventSink,
    watcherobot_version: str | None = None,
) -> PublishResult:
    """Check, upload and submit one Application for catalog review."""

    events.emit(
        ProgressEvent(stage="checking", message="正在检查 Application")
    )
    application = check_application(
        application_dir,
        watcherobot_version=watcherobot_version,
    )
    files = prepare_space_upload_files(application_dir, application)

    events.emit(
        ProgressEvent(
            stage="authenticating",
            message="正在验证 Hugging Face 登录",
        )
    )
    identity, token = _load_verified_identity(
        credentials=credentials,
        identity_hub=identity_hub,
    )
    space_id = f"{identity.username}/WatcherRobot-{application.app_id}"
    space_url = f"https://huggingface.co/spaces/{space_id}"

    events.emit(
        ProgressEvent(
            stage="ensuring_space",
            message="正在创建或确认公开源码 Space",
            data={"space_id": space_id},
        )
    )
    try:
        publish_hub.ensure_public_space(
            token,
            space_id=space_id,
            sdk=SPACE_SDK,
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "同名 Space 不属于 Watcher Desktop 发布工具，已拒绝覆盖",
            details={"space_id": space_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("无法创建或确认 Hugging Face Space", exc)

    events.emit(
        ProgressEvent(
            stage="uploading_source",
            message="正在上传 Application 源码",
            data={"space_id": space_id},
        )
    )
    try:
        publish_hub.replace_space_files(
            token,
            space_id=space_id,
            files=files,
            commit_message=(
                f"Publish {application.app_id} {application.version}"
            ),
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "同名 Space 不属于 Watcher Desktop 发布工具，已拒绝覆盖",
            details={"space_id": space_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("上传 Application 源码失败", exc)

    events.emit(
        ProgressEvent(
            stage="resolving_commit",
            message="正在读取固定源码 commit",
            data={"space_id": space_id},
        )
    )
    try:
        revision = publish_hub.get_space_head(token, space_id=space_id)
    except HubError as exc:
        raise _remote_error("无法读取完整 Space commit", exc)

    source_details: dict[str, object] = {
        "space_id": space_id,
        "commit": revision.commit,
        "source_url": revision.url,
    }
    events.emit(
        ProgressEvent(
            stage="updating_catalog",
            message="正在准备官方应用名单申请",
            data={"space_id": space_id, "commit": revision.commit},
        )
    )
    try:
        catalog = publish_hub.read_catalog(
            token,
            repo_id=CATALOG_REPO_ID,
            path=CATALOG_PATH,
        )
        open_pull_requests = publish_hub.list_open_catalog_pull_requests(
            token,
            repo_id=CATALOG_REPO_ID,
            author=identity.username,
        )
        plan = plan_catalog_submission(
            catalog,
            open_pull_requests=open_pull_requests,
            space_id=space_id,
            commit=revision.commit,
        )
    except CatalogDocumentError as exc:
        raise PublishError(
            ErrorCode.CATALOG_INVALID,
            "官方 Application 名单结构无效，已停止提交",
            details=source_details,
        ) from exc
    except CatalogPullRequestConflict as exc:
        raise PublishError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "该 Application 已有其他 commit 的待审核名单 PR",
            details={**source_details, "pr_url": exc.pull_request.url},
        ) from exc
    except HubError as exc:
        raise _remote_error(
            "读取官方 Application 名单或 PR 状态失败",
            exc,
            details=source_details,
        )

    if plan.status == "already_listed":
        return PublishResult(
            space_id=space_id,
            commit=revision.commit,
            space_url=space_url,
            source_url=revision.url,
            pr_url="",
            pr_status="already_listed",
        )
    if plan.status == "pending":
        assert plan.pull_request is not None
        return PublishResult(
            space_id=space_id,
            commit=revision.commit,
            space_url=space_url,
            source_url=revision.url,
            pr_url=plan.pull_request.url,
            pr_status="pending",
        )

    assert plan.content is not None
    try:
        pull_request = publish_hub.create_catalog_pull_request(
            token,
            repo_id=CATALOG_REPO_ID,
            path=CATALOG_PATH,
            content=plan.content,
            parent_commit=plan.parent_commit,
            title=catalog_pull_request_title(space_id, revision.commit),
            description=(
                f"Request review for `{space_id}` at `{revision.commit}`.\n\n"
                f"Source: {revision.url}"
            ),
        )
    except HubCatalogConflict as exc:
        raise PublishError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "官方 Application 名单已变化，请重新发布以生成最新申请",
            details=source_details,
        ) from exc
    except HubError as exc:
        raise _remote_error(
            "创建官方 Application 名单 PR 失败",
            exc,
            details=source_details,
        )
    return PublishResult(
        space_id=space_id,
        commit=revision.commit,
        space_url=space_url,
        source_url=revision.url,
        pr_url=pull_request.url,
        pr_status="pending",
    )


@dataclass(frozen=True)
class _VerifiedIdentity:
    username: str
    display_name: str = ""


def _load_verified_identity(
    *,
    credentials: CredentialStore,
    identity_hub: HubClient,
) -> tuple[_VerifiedIdentity, AccessToken]:
    try:
        status = login_status(credentials=credentials, hub=identity_hub)
    except LoginError as exc:
        raise PublishError(exc.code, str(exc)) from exc
    if not status.logged_in:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            "请先登录 Hugging Face 后再发布 Application",
        )
    try:
        token = credentials.load()
    except CredentialStoreError as exc:
        raise PublishError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "无法读取 Watcher Hugging Face 系统凭据",
        ) from exc
    if token is None:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            "Hugging Face 登录凭据不存在，请重新登录",
        )
    return (
        _VerifiedIdentity(
            username=status.username,
            display_name=status.display_name,
        ),
        token,
    )


def _remote_error(
    message: str,
    error: Exception,
    *,
    details: dict[str, object] | None = None,
) -> PublishError:
    code = (
        ErrorCode.AUTH_REQUIRED
        if isinstance(error, HubAuthenticationError)
        else ErrorCode.REMOTE_ERROR
    )
    return PublishError(code, message, details=details)
