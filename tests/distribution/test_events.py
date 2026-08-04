from __future__ import annotations

import io
import json

from watcherobot.distribution.events import (
    ErrorCode,
    ErrorEvent,
    ExitCode,
    JsonLineEventWriter,
    ProgressEvent,
    ResultEvent,
    exit_code_for,
)


def test_json_line_writer_emits_one_compact_object_per_event() -> None:
    output = io.StringIO()
    writer = JsonLineEventWriter(output)

    writer.emit(
        ProgressEvent(
            stage="checking",
            message="正在检查 Application",
            data={"path": "D:/apps/demo"},
        )
    )
    writer.emit(ResultEvent(data={"app_id": "com.orulink.demo"}))
    writer.emit(
        ErrorEvent(
            code=ErrorCode.APP_MANIFEST_INVALID,
            message="app.json 无效",
            details={"field": "version"},
        )
    )

    assert [json.loads(line) for line in output.getvalue().splitlines()] == [
        {
            "type": "progress",
            "stage": "checking",
            "message": "正在检查 Application",
            "data": {"path": "D:/apps/demo"},
        },
        {
            "type": "result",
            "ok": True,
            "data": {"app_id": "com.orulink.demo"},
        },
        {
            "type": "error",
            "ok": False,
            "code": "app_manifest_invalid",
            "message": "app.json 无效",
            "details": {"field": "version"},
        },
    ]
    assert output.getvalue().endswith("\n")


def test_event_models_do_not_share_mutable_default_data() -> None:
    first = ProgressEvent(stage="one", message="first")
    second = ProgressEvent(stage="two", message="second")

    first.data["changed"] = True

    assert second.data == {}
    assert ResultEvent().data == {}
    assert ErrorEvent(
        code=ErrorCode.INTERNAL_ERROR,
        message="failed",
    ).details == {}


def test_stable_error_categories_map_to_process_exit_codes() -> None:
    validation_codes = {
        ErrorCode.APP_MANIFEST_MISSING,
        ErrorCode.APP_ENTRYPOINT_MISSING,
        ErrorCode.APP_MANIFEST_INVALID,
        ErrorCode.APP_SDK_INCOMPATIBLE,
        ErrorCode.APP_DEPENDENCY_INVALID,
        ErrorCode.APP_CONTENT_FORBIDDEN,
    }

    assert {
        exit_code_for(error_code) for error_code in validation_codes
    } == {ExitCode.VALIDATION_ERROR}
    assert exit_code_for(ErrorCode.AUTH_REQUIRED) is ExitCode.AUTH_ERROR
    assert exit_code_for(ErrorCode.AUTH_DENIED) is ExitCode.AUTH_ERROR
    assert exit_code_for(ErrorCode.AUTH_EXPIRED) is ExitCode.AUTH_ERROR
    assert (
        exit_code_for(ErrorCode.AUTH_NETWORK_ERROR)
        is ExitCode.REMOTE_ERROR
    )
    assert exit_code_for(ErrorCode.REMOTE_ERROR) is ExitCode.REMOTE_ERROR
    assert (
        exit_code_for(ErrorCode.SPACE_OWNERSHIP_CONFLICT)
        is ExitCode.REMOTE_ERROR
    )
    assert exit_code_for(ErrorCode.CATALOG_INVALID) is ExitCode.REMOTE_ERROR
    assert (
        exit_code_for(ErrorCode.CATALOG_PR_CONFLICT)
        is ExitCode.REMOTE_ERROR
    )
    assert (
        exit_code_for(ErrorCode.OPERATION_CANCELLED)
        is ExitCode.CANCELLED
    )
    assert exit_code_for(ErrorCode.INTERNAL_ERROR) is ExitCode.INTERNAL_ERROR
