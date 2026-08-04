"""Application distribution contracts shared by CLI and Desktop callers."""

from .check import ApplicationCheckResult, check_application
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
from .ports import (
    AccessToken,
    CredentialStore,
    HubClient,
    HubIdentity,
    OAuthClient,
    OAuthRequest,
)
from .source_files import (
    ApplicationSourceError,
    collect_application_source_files,
)

__all__ = [
    "ApplicationCheckResult",
    "ApplicationSourceError",
    "DistributionEvent",
    "AccessToken",
    "CredentialStore",
    "ErrorCode",
    "ErrorEvent",
    "EventSink",
    "ExitCode",
    "HubClient",
    "HubIdentity",
    "JsonLineEventWriter",
    "OAuthClient",
    "OAuthRequest",
    "ProgressEvent",
    "ResultEvent",
    "check_application",
    "collect_application_source_files",
    "exit_code_for",
]
