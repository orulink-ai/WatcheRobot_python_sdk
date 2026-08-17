"""Stable machine-readable events for Application distribution commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Protocol, TextIO, Union


class ErrorCode(str, Enum):
    """Stable error identifiers consumed by Desktop and other callers."""

    APP_MANIFEST_MISSING = "app_manifest_missing"
    APP_ENTRYPOINT_MISSING = "app_entrypoint_missing"
    APP_MANIFEST_INVALID = "app_manifest_invalid"
    APP_SDK_INCOMPATIBLE = "app_sdk_incompatible"
    APP_DEPENDENCY_INVALID = "app_dependency_invalid"
    APP_CONTENT_FORBIDDEN = "app_content_forbidden"
    AUTH_REQUIRED = "auth_required"
    AUTH_DENIED = "auth_denied"
    AUTH_EXPIRED = "auth_expired"
    AUTH_INVALID_RESPONSE = "auth_invalid_response"
    AUTH_NETWORK_ERROR = "auth_network_error"
    CREDENTIAL_STORE_ERROR = "credential_store_error"
    SPACE_OWNERSHIP_CONFLICT = "space_ownership_conflict"
    CATALOG_INVALID = "catalog_invalid"
    CATALOG_PR_CONFLICT = "catalog_pr_conflict"
    REMOTE_ERROR = "remote_error"
    OPERATION_CANCELLED = "operation_cancelled"
    RUNTIME_MANIFEST_INVALID = "runtime_manifest_invalid"
    RUNTIME_RESOURCES_MISSING = "runtime_resources_missing"
    RUNTIME_PYTHON_INTEGRITY_FAILED = "runtime_python_integrity_failed"
    RUNTIME_UV_INTEGRITY_FAILED = "runtime_uv_integrity_failed"
    RUNTIME_SDK_WHEEL_INTEGRITY_FAILED = "runtime_sdk_wheel_integrity_failed"
    INTERNAL_ERROR = "internal_error"


class ExitCode(IntEnum):
    """Process exit codes for distribution commands."""

    SUCCESS = 0
    VALIDATION_ERROR = 2
    AUTH_ERROR = 3
    REMOTE_ERROR = 4
    INTERNAL_ERROR = 5
    CANCELLED = 130


@dataclass
class ProgressEvent:
    """Report an in-progress stage without implying completion."""

    stage: str
    message: str
    data: dict[str, object] = field(default_factory=dict)
    type: str = field(default="progress", init=False)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "stage": self.stage,
            "message": self.message,
        }
        if self.data:
            payload["data"] = dict(self.data)
        return payload


@dataclass
class ResultEvent:
    """Report the single successful result of one command."""

    data: dict[str, object] = field(default_factory=dict)
    type: str = field(default="result", init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "ok": True,
            "data": dict(self.data),
        }


@dataclass
class ErrorEvent:
    """Report a sanitized failure suitable for machine consumers."""

    code: ErrorCode
    message: str
    details: dict[str, object] = field(default_factory=dict)
    type: str = field(default="error", init=False)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "ok": False,
            "code": self.code.value,
            "message": self.message,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


DistributionEvent = Union[ProgressEvent, ResultEvent, ErrorEvent]


class EventSink(Protocol):
    """Destination for distribution events."""

    def emit(self, event: DistributionEvent) -> None: ...


class JsonLineEventWriter:
    """Write one compact JSON object per line and flush immediately."""

    def __init__(self, output: TextIO) -> None:
        self._output = output

    def emit(self, event: DistributionEvent) -> None:
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        self._output.write(encoded + "\n")
        self._output.flush()


_VALIDATION_ERROR_CODES = frozenset(
    {
        ErrorCode.APP_MANIFEST_MISSING,
        ErrorCode.APP_ENTRYPOINT_MISSING,
        ErrorCode.APP_MANIFEST_INVALID,
        ErrorCode.APP_SDK_INCOMPATIBLE,
        ErrorCode.APP_DEPENDENCY_INVALID,
        ErrorCode.APP_CONTENT_FORBIDDEN,
    }
)
_AUTH_ERROR_CODES = frozenset(
    {
        ErrorCode.AUTH_REQUIRED,
        ErrorCode.AUTH_DENIED,
        ErrorCode.AUTH_EXPIRED,
        ErrorCode.AUTH_INVALID_RESPONSE,
        ErrorCode.CREDENTIAL_STORE_ERROR,
    }
)
_REMOTE_ERROR_CODES = frozenset(
    {
        ErrorCode.AUTH_NETWORK_ERROR,
        ErrorCode.SPACE_OWNERSHIP_CONFLICT,
        ErrorCode.CATALOG_INVALID,
        ErrorCode.CATALOG_PR_CONFLICT,
        ErrorCode.REMOTE_ERROR,
    }
)


def exit_code_for(error_code: ErrorCode) -> ExitCode:
    """Map a stable error code to a stable process exit category."""

    if error_code in _VALIDATION_ERROR_CODES:
        return ExitCode.VALIDATION_ERROR
    if error_code in _AUTH_ERROR_CODES:
        return ExitCode.AUTH_ERROR
    if error_code in _REMOTE_ERROR_CODES:
        return ExitCode.REMOTE_ERROR
    if error_code is ErrorCode.OPERATION_CANCELLED:
        return ExitCode.CANCELLED
    return ExitCode.INTERNAL_ERROR
