"""Application distribution CLI shared by the SDK and Desktop sidecar."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationCompatibilityError,
    ApplicationManifestError,
)

from .check import check_application
from .credentials import SystemCredentialStore
from .download import DownloadError, DownloadResult, download_application_snapshot
from .events import (
    DistributionEvent,
    ErrorCode,
    ErrorEvent,
    EventSink,
    ExitCode,
    JsonLineEventWriter,
    ProgressEvent,
    ResultEvent,
    exit_code_for,
)
from .hf_marketplace import HuggingFaceMarketplaceHubClient
from .hf_publish import HuggingFacePublishHubClient
from .hub_http import HuggingFaceHubClient
from .login import LoginError, LoginResult, LoginStatus, login, login_status, logout
from .marketplace import (
    MarketplaceError,
    OfficialMarketplace,
    load_official_marketplace,
)
from .oauth_http import HuggingFaceOAuthClient
from .ports import (
    CredentialStore,
    HubClient,
    MarketplaceHubClient,
    OAuthClient,
    PublishHubClient,
)
from .publish import PublishError, PublishResult, publish_application
from .source_files import ApplicationSourceError
from .submit import SubmitError, SubmitResult, submit_application


DISTRIBUTION_COMMANDS = frozenset(
    {
        "check",
        "login",
        "logout",
        "publish",
        "submit",
        "marketplace",
        "download",
    }
)
_JSONL_HELP = (
    "Emit stable JSON Lines for Desktop automation; for manual use, omit "
    "--jsonl"
)


def add_distribution_commands(
    app_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register only commands that belong to the distribution process."""

    check = app_commands.add_parser(
        "check",
        help="Validate an Application source directory",
        description="Validate app.json, app.py, dependencies, and source files.",
    )
    check.add_argument(
        "application_dir",
        type=Path,
        help="Application source directory",
    )
    _add_jsonl_argument(check)
    login_command = app_commands.add_parser(
        "login",
        help="Sign in to Hugging Face for publishing",
        description=(
            "Authorize the SDK with Hugging Face Device Flow. Omit --jsonl "
            "for interactive use."
        ),
    )
    login_mode = login_command.add_mutually_exclusive_group()
    login_mode.add_argument(
        "--status",
        action="store_true",
        help="Check the saved Hugging Face identity without signing in",
    )
    login_mode.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing valid login",
    )
    _add_jsonl_argument(login_command)
    logout_command = app_commands.add_parser(
        "logout",
        help="Remove only the SDK's saved Hugging Face credential",
    )
    _add_jsonl_argument(logout_command)
    publish = app_commands.add_parser(
        "publish",
        help="Publish Application source to its public Space",
        description=(
            "Validate and publish one public immutable source snapshot. This "
            "command does not modify the official marketplace catalog."
        ),
    )
    publish.add_argument(
        "application_dir",
        type=Path,
        help="Application source directory",
    )
    _add_jsonl_argument(publish)
    submit = app_commands.add_parser(
        "submit",
        help="Submit a published commit for official marketplace review",
        description=(
            "Validate an already-published immutable source snapshot, then "
            "open or reuse its official marketplace pull request. This "
            "command never uploads Application source."
        ),
    )
    submit.add_argument(
        "application_dir",
        type=Path,
        help="Local project matching the published Application snapshot",
    )
    submit.add_argument(
        "--commit",
        help=(
            "Exact 40-character published commit; omit to submit the current "
            "Space HEAD"
        ),
    )
    _add_jsonl_argument(submit)
    marketplace = app_commands.add_parser(
        "marketplace",
        help="List reviewed Applications in the official marketplace",
        description=(
            "Load the reviewed official marketplace. The default is a compact "
            "table for people."
        ),
    )
    marketplace_output = marketplace.add_mutually_exclusive_group()
    marketplace_output.add_argument(
        "--details",
        action="store_true",
        help="Show complete metadata, immutable source, and dependencies",
    )
    marketplace_output.add_argument(
        "--jsonl",
        action="store_true",
        help=_JSONL_HELP,
    )
    download = app_commands.add_parser(
        "download",
        help="Download and validate one immutable source snapshot",
    )
    download.add_argument(
        "--space-id",
        required=True,
        help="Hugging Face Space, for example user/WatcherRobot-com.example.app",
    )
    download.add_argument(
        "--commit",
        required=True,
        help="Exact 40-character source commit from the marketplace",
    )
    download.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Existing empty staging directory",
    )
    _add_jsonl_argument(download)


def _add_jsonl_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jsonl", action="store_true", help=_JSONL_HELP)


def build_parser() -> argparse.ArgumentParser:
    """Build the restricted parser used by ``watcher-distribution``."""

    parser = argparse.ArgumentParser(
        prog="watcher-distribution",
        description="WatcheRobot Desktop Application distribution sidecar.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    app = commands.add_parser(
        "app",
        help="Validate, authenticate, publish, and download Applications",
        description=(
            "Short-lived Application distribution commands. These commands "
            "never start the Daemon."
        ),
        epilog="For manual use, omit --jsonl. Desktop automation uses --jsonl.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    app_commands = app.add_subparsers(
        dest="app_command",
        required=True,
        title="Application distribution commands",
        metavar="COMMAND",
    )
    add_distribution_commands(app_commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one short-lived distribution command without starting the Daemon."""

    return run_command(build_parser().parse_args(argv))


def is_distribution_command(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "command", None) == "app"
        and getattr(args, "app_command", None) in DISTRIBUTION_COMMANDS
    )


def run_command(args: argparse.Namespace) -> int:
    """Dispatch a parsed distribution command from either CLI entrypoint."""

    if not is_distribution_command(args):
        raise ValueError("unsupported Application distribution command")
    if args.app_command == "check":
        return _run_application_check(args)
    if args.app_command == "login":
        return _run_application_login(args)
    if args.app_command == "logout":
        return _run_application_logout(args)
    if args.app_command == "publish":
        return _run_application_publish(args)
    if args.app_command == "submit":
        return _run_application_submit(args)
    if args.app_command == "marketplace":
        return _run_application_marketplace(args)
    if args.app_command == "download":
        return _run_application_download(args)
    raise AssertionError("unreachable distribution command")


def _run_application_check(args: argparse.Namespace) -> int:
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    if event_writer is not None:
        event_writer.emit(
            ProgressEvent(stage="checking", message="Validating Application")
        )
    try:
        result = check_application(args.application_dir)
    except ApplicationCompatibilityError as exc:
        return _print_application_check_error(
            ErrorCode.APP_SDK_INCOMPATIBLE,
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationManifestError as exc:
        return _print_application_check_error(
            _manifest_error_code(exc),
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationSourceError as exc:
        return _print_application_check_error(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            str(exc),
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        _print_labeled_block(
            "Application validated",
            (
                ("Name", result.name),
                ("ID", result.app_id),
                ("Version", result.version),
                ("SDK requirement", result.requires_watcherobot),
                ("Dependencies", _format_dependencies(result.dependencies)),
                ("Author", result.author or "Not specified"),
                ("Description", result.description or "Not specified"),
            ),
        )
    return ExitCode.SUCCESS


@dataclass(frozen=True)
class _AuthDependencies:
    oauth: OAuthClient
    credentials: CredentialStore
    hub: HubClient


class _HumanAuthEventSink:
    """Render public Device Flow instructions for terminal users."""

    def emit(self, event: DistributionEvent) -> None:
        if not isinstance(event, ProgressEvent):
            return
        print(event.message)
        verification_uri = event.data.get("verification_uri")
        user_code = event.data.get("user_code")
        expires_in = event.data.get("expires_in")
        if isinstance(verification_uri, str):
            print(f"Open: {verification_uri}")
        if isinstance(user_code, str):
            print(f"Enter code: {user_code}")
        if isinstance(expires_in, int):
            print(f"Code expires in: {expires_in} seconds")


def _build_auth_dependencies() -> _AuthDependencies:
    return _AuthDependencies(
        oauth=HuggingFaceOAuthClient(),
        credentials=SystemCredentialStore(),
        hub=HuggingFaceHubClient(),
    )


def _run_application_login(args: argparse.Namespace) -> int:
    dependencies = _build_auth_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    result: LoginStatus | LoginResult
    try:
        if args.status:
            result = login_status(
                credentials=dependencies.credentials,
                hub=dependencies.hub,
            )
        else:
            events: EventSink = event_writer or _HumanAuthEventSink()
            result = login(
                oauth=dependencies.oauth,
                credentials=dependencies.credentials,
                hub=dependencies.hub,
                events=events,
                force=bool(args.force),
            )
    except KeyboardInterrupt:
        return _print_auth_error(
            ErrorCode.OPERATION_CANCELLED,
            "Hugging Face login cancelled",
            event_writer=event_writer,
        )
    except LoginError as exc:
        return _print_auth_error(exc.code, str(exc), event_writer=event_writer)

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    elif isinstance(result, LoginStatus):
        if result.logged_in:
            print(f"Logged in to Hugging Face as: {result.username}")
        else:
            print("Not logged in to Hugging Face")
    elif result.reused:
        print(f"Reusing existing Hugging Face login: {result.username}")
    else:
        print(f"Hugging Face login successful: {result.username}")
    return ExitCode.SUCCESS


def _run_application_logout(args: argparse.Namespace) -> int:
    dependencies = _build_auth_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    try:
        result = logout(credentials=dependencies.credentials)
    except LoginError as exc:
        return _print_auth_error(exc.code, str(exc), event_writer=event_writer)
    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        print("Signed out of Watcher's Hugging Face login")
    return ExitCode.SUCCESS


@dataclass(frozen=True)
class _PublishDependencies:
    credentials: CredentialStore
    identity_hub: HubClient
    publish_hub: PublishHubClient


class _HumanPublishEventSink:
    """Render non-sensitive publishing progress for terminal users."""

    def emit(self, event: DistributionEvent) -> None:
        if isinstance(event, ProgressEvent):
            print(event.message, file=sys.stderr)


def _build_publish_dependencies() -> _PublishDependencies:
    return _PublishDependencies(
        credentials=SystemCredentialStore(),
        identity_hub=HuggingFaceHubClient(),
        publish_hub=HuggingFacePublishHubClient(),
    )


def _run_application_publish(args: argparse.Namespace) -> int:
    dependencies = _build_publish_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    events: EventSink = event_writer or _HumanPublishEventSink()
    try:
        result = publish_application(
            args.application_dir,
            credentials=dependencies.credentials,
            identity_hub=dependencies.identity_hub,
            publish_hub=dependencies.publish_hub,
            events=events,
        )
    except KeyboardInterrupt:
        return _print_publish_error(
            ErrorCode.OPERATION_CANCELLED,
            "Application source publishing cancelled",
            event_writer=event_writer,
        )
    except ApplicationCompatibilityError as exc:
        return _print_publish_error(
            ErrorCode.APP_SDK_INCOMPATIBLE,
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationManifestError as exc:
        return _print_publish_error(
            _manifest_error_code(exc),
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationSourceError as exc:
        return _print_publish_error(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            str(exc),
            event_writer=event_writer,
        )
    except PublishError as exc:
        return _print_publish_error(
            exc.code,
            str(exc),
            details=exc.details,
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        _print_publish_result(result)
    return ExitCode.SUCCESS


def _print_publish_result(result: PublishResult) -> None:
    fields: tuple[tuple[str, str], ...] = (
        ("Space", result.space_id),
        ("Commit", result.commit),
        ("Space URL", result.space_url),
        ("Source", result.source_url),
    )
    _print_labeled_block("Application source published", fields)


def _print_publish_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
    details: dict[str, object] | None = None,
) -> int:
    safe_details = dict(details or {})
    if event_writer is not None:
        event_writer.emit(
            ErrorEvent(code=code, message=message, details=safe_details)
        )
    else:
        print(f"Application source publishing failed: {message}", file=sys.stderr)
        source_url = safe_details.get("source_url")
        if isinstance(source_url, str):
            print(f"Uploaded source: {source_url}", file=sys.stderr)
    return exit_code_for(code)


def _run_application_submit(args: argparse.Namespace) -> int:
    dependencies = _build_publish_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    events: EventSink = event_writer or _HumanPublishEventSink()
    try:
        result = submit_application(
            args.application_dir,
            commit=args.commit,
            credentials=dependencies.credentials,
            identity_hub=dependencies.identity_hub,
            publish_hub=dependencies.publish_hub,
            events=events,
        )
    except KeyboardInterrupt:
        return _print_submit_error(
            ErrorCode.OPERATION_CANCELLED,
            "Catalog submission cancelled",
            event_writer=event_writer,
        )
    except ApplicationCompatibilityError as exc:
        return _print_submit_error(
            ErrorCode.APP_SDK_INCOMPATIBLE,
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationManifestError as exc:
        return _print_submit_error(
            _manifest_error_code(exc),
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationSourceError as exc:
        return _print_submit_error(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            str(exc),
            event_writer=event_writer,
        )
    except SubmitError as exc:
        return _print_submit_error(
            exc.code,
            str(exc),
            details=exc.details,
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        _print_submit_result(result)
    return ExitCode.SUCCESS


def _print_submit_result(result: SubmitResult) -> None:
    fields: list[tuple[str, str]] = [
        ("Space", result.space_id),
        ("Commit", result.commit),
        ("Source", result.source_url),
        ("Catalog status", _format_catalog_status(result.pr_status)),
    ]
    if result.pr_url:
        fields.append(("Catalog PR", result.pr_url))
    _print_labeled_block("Catalog submission ready", tuple(fields))


def _print_submit_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
    details: dict[str, object] | None = None,
) -> int:
    safe_details = dict(details or {})
    if event_writer is not None:
        event_writer.emit(
            ErrorEvent(code=code, message=message, details=safe_details)
        )
    else:
        print(f"Catalog submission failed: {message}", file=sys.stderr)
        source_url = safe_details.get("source_url")
        pr_url = safe_details.get("pr_url")
        if isinstance(source_url, str):
            print(f"Published source: {source_url}", file=sys.stderr)
        if isinstance(pr_url, str):
            print(f"Existing catalog PR: {pr_url}", file=sys.stderr)
    return exit_code_for(code)


@dataclass(frozen=True)
class _MarketplaceDependencies:
    hub: MarketplaceHubClient


class _HumanMarketplaceEventSink:
    """Render non-sensitive official marketplace progress."""

    def emit(self, event: DistributionEvent) -> None:
        if (
            isinstance(event, ProgressEvent)
            and event.stage == "fetching_catalog"
        ):
            print(f"{event.message}...", file=sys.stderr)


def _build_marketplace_dependencies() -> _MarketplaceDependencies:
    return _MarketplaceDependencies(hub=HuggingFaceMarketplaceHubClient())


def _run_application_marketplace(args: argparse.Namespace) -> int:
    dependencies = _build_marketplace_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    events: EventSink = event_writer or _HumanMarketplaceEventSink()
    try:
        result = load_official_marketplace(hub=dependencies.hub, events=events)
    except KeyboardInterrupt:
        return _print_marketplace_error(
            ErrorCode.OPERATION_CANCELLED,
            "Loading the Application marketplace was cancelled",
            event_writer=event_writer,
        )
    except MarketplaceError as exc:
        return _print_marketplace_error(
            exc.code,
            str(exc),
            details=exc.details,
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        _print_marketplace_result(result, details=bool(args.details))
    return ExitCode.SUCCESS


def _print_marketplace_result(
    result: OfficialMarketplace,
    *,
    details: bool,
) -> None:
    print("Application Marketplace")
    print(f"Catalog commit: {result.catalog_commit}")
    print(f"Applications: {len(result.applications)}")
    if not result.applications:
        return
    print()
    if details:
        _print_marketplace_details(result)
        return

    header = (
        f"{'STATUS':<14}{'VERSION':<13}{'NAME':<25}APPLICATION ID"
    )
    print(header)
    print("-" * len(header))
    for application in result.applications:
        status = "Compatible" if application.compatible else "Incompatible"
        print(
            f"{status:<14}"
            f"{_truncate(application.version, 11):<13}"
            f"{_truncate(application.name, 23):<25}"
            f"{application.app_id}"
        )


def _print_marketplace_details(result: OfficialMarketplace) -> None:
    for index, application in enumerate(result.applications, start=1):
        if index > 1:
            print()
        _print_labeled_block(
            f"{index}. {application.name}",
            (
                ("ID", application.app_id),
                ("Version", application.version),
                (
                    "Compatibility",
                    "Compatible" if application.compatible else "Incompatible",
                ),
                ("SDK requirement", application.requires_watcherobot),
                (
                    "Dependencies",
                    _format_dependencies(application.dependencies),
                ),
                ("Author", application.author or "Not specified"),
                ("Description", application.description or "Not specified"),
                ("Source", application.source_url),
                ("Commit", application.commit),
            ),
        )


def _print_marketplace_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
    details: dict[str, object] | None = None,
) -> int:
    safe_details = dict(details or {})
    if event_writer is not None:
        event_writer.emit(
            ErrorEvent(code=code, message=message, details=safe_details)
        )
    else:
        print(message, file=sys.stderr)
    return exit_code_for(code)


@dataclass(frozen=True)
class _DownloadDependencies:
    hub: MarketplaceHubClient


class _HumanDownloadEventSink:
    """Render non-sensitive snapshot download progress."""

    def emit(self, event: DistributionEvent) -> None:
        if isinstance(event, ProgressEvent):
            print(event.message, file=sys.stderr)


def _build_download_dependencies() -> _DownloadDependencies:
    return _DownloadDependencies(hub=HuggingFaceMarketplaceHubClient())


def _run_application_download(args: argparse.Namespace) -> int:
    dependencies = _build_download_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    events: EventSink = event_writer or _HumanDownloadEventSink()
    try:
        result = download_application_snapshot(
            space_id=args.space_id,
            commit=args.commit,
            target=args.target,
            hub=dependencies.hub,
            events=events,
        )
    except KeyboardInterrupt:
        return _print_download_error(
            ErrorCode.OPERATION_CANCELLED,
            "Application download cancelled",
            event_writer=event_writer,
        )
    except ApplicationCompatibilityError as exc:
        return _print_download_error(
            ErrorCode.APP_SDK_INCOMPATIBLE,
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationManifestError as exc:
        return _print_download_error(
            _manifest_error_code(exc),
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationSourceError as exc:
        return _print_download_error(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            str(exc),
            event_writer=event_writer,
        )
    except DownloadError as exc:
        return _print_download_error(
            exc.code,
            str(exc),
            details=exc.details,
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        _print_download_result(result)
    return ExitCode.SUCCESS


def _print_download_result(result: DownloadResult) -> None:
    _print_labeled_block(
        "Application downloaded",
        (
            ("Name", result.application.name),
            ("ID", result.application.app_id),
            ("Version", result.application.version),
            ("Space", result.space_id),
            ("Commit", result.commit),
            ("Source", result.source_url),
            ("Staging", str(result.target)),
        ),
    )


def _print_download_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
    details: dict[str, object] | None = None,
) -> int:
    safe_details = dict(details or {})
    if event_writer is not None:
        event_writer.emit(
            ErrorEvent(code=code, message=message, details=safe_details)
        )
    else:
        print(f"Application download failed: {message}", file=sys.stderr)
    return exit_code_for(code)


def _print_auth_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
) -> int:
    if event_writer is not None:
        event_writer.emit(ErrorEvent(code=code, message=message))
    else:
        print(message, file=sys.stderr)
    return exit_code_for(code)


def _print_application_check_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
) -> int:
    if event_writer is not None:
        event_writer.emit(ErrorEvent(code=code, message=message))
    else:
        print(f"Application validation failed: {message}", file=sys.stderr)
    return exit_code_for(code)


def _manifest_error_code(error: ApplicationManifestError) -> ErrorCode:
    try:
        return ErrorCode(error.code)
    except ValueError:
        return ErrorCode.APP_MANIFEST_INVALID


def _print_labeled_block(
    title: str,
    fields: tuple[tuple[str, str], ...],
) -> None:
    print(title)
    print()
    label_width = max(len(label) + 1 for label, _value in fields)
    for label, value in fields:
        print(f"{label + ':':<{label_width}}  {value}")


def _format_dependencies(dependencies: tuple[str, ...]) -> str:
    return ", ".join(dependencies) if dependencies else "None"


def _format_catalog_status(status: str) -> str:
    return {
        "pending": "Pending review",
        "already_listed": "Already listed",
    }.get(status, status.replace("_", " ").title())


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."
