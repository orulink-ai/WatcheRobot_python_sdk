from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.hf_publish import HuggingFacePublishHubClient
from watcherobot.distribution.publish import PublishError, PublishResult
from watcherobot.distribution.source_files import ApplicationSourceError


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
COMMIT = "a" * 40


def _result() -> PublishResult:
    return PublishResult(
        space_id=SPACE_ID,
        commit=COMMIT,
        space_url=f"https://huggingface.co/spaces/{SPACE_ID}",
        source_url=(
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
        ),
        pr_url="https://huggingface.co/datasets/catalog/discussions/7",
        pr_status="pending",
    )


def _install_success(monkeypatch):
    dependencies = SimpleNamespace(
        credentials=object(),
        identity_hub=object(),
        publish_hub=object(),
    )
    calls: list[Path] = []

    def fake_publish(application_dir: Path, **kwargs):
        calls.append(application_dir)
        assert kwargs["credentials"] is dependencies.credentials
        assert kwargs["identity_hub"] is dependencies.identity_hub
        assert kwargs["publish_hub"] is dependencies.publish_hub
        kwargs["events"].emit(
            ProgressEvent(
                stage="checking",
                message="正在检查 Application",
            )
        )
        kwargs["events"].emit(
            ProgressEvent(
                stage="updating_catalog",
                message="正在准备官方应用名单申请",
                data={"space_id": SPACE_ID, "commit": COMMIT},
            )
        )
        return _result()

    monkeypatch.setattr(
        "watcherobot.cli._build_publish_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.cli.publish_application",
        fake_publish,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("app publish must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return calls


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def test_cli_publish_jsonl_reuses_service_and_never_starts_daemon(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = _install_success(monkeypatch)

    exit_code = main(["app", "publish", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert calls == [tmp_path]
    assert _json_lines(captured.out) == [
        {
            "type": "progress",
            "stage": "checking",
            "message": "正在检查 Application",
        },
        {
            "type": "progress",
            "stage": "updating_catalog",
            "message": "正在准备官方应用名单申请",
            "data": {"space_id": SPACE_ID, "commit": COMMIT},
        },
        {"type": "result", "ok": True, "data": _result().to_dict()},
    ]
    assert "token" not in captured.out.lower()


def test_cli_publish_human_output_shows_fixed_source_and_pr(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "publish", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "正在检查 Application" in captured.out
    assert SPACE_ID in captured.out
    assert f"/tree/{COMMIT}" in captured.out
    assert "discussions/7" in captured.out
    assert "pending" in captured.out


def test_cli_publish_jsonl_emits_stable_remote_error_with_partial_result(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    dependencies = SimpleNamespace(
        credentials=object(),
        identity_hub=object(),
        publish_hub=object(),
    )
    monkeypatch.setattr(
        "watcherobot.cli._build_publish_dependencies",
        lambda: dependencies,
        raising=False,
    )

    def fail_publish(*args, **kwargs):
        raise PublishError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "该 Application 已有其他 commit 的待审核名单 PR",
            details={
                "space_id": SPACE_ID,
                "commit": COMMIT,
                "source_url": (
                    f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
                ),
                "pr_url": "https://huggingface.co/datasets/catalog/discussions/6",
            },
        )

    monkeypatch.setattr(
        "watcherobot.cli.publish_application",
        fail_publish,
        raising=False,
    )

    exit_code = main(["app", "publish", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "error",
            "ok": False,
            "code": "catalog_pr_conflict",
            "message": "该 Application 已有其他 commit 的待审核名单 PR",
            "details": {
                "space_id": SPACE_ID,
                "commit": COMMIT,
                "source_url": (
                    f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
                ),
                "pr_url": "https://huggingface.co/datasets/catalog/discussions/6",
            },
        }
    ]


def test_cli_publish_maps_local_source_error_to_validation_exit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._build_publish_dependencies",
        lambda: SimpleNamespace(
            credentials=object(),
            identity_hub=object(),
            publish_hub=object(),
        ),
        raising=False,
    )

    def fail_publish(*args, **kwargs):
        raise ApplicationSourceError("forbidden local content")

    monkeypatch.setattr(
        "watcherobot.cli.publish_application",
        fail_publish,
        raising=False,
    )

    exit_code = main(["app", "publish", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == ""
    assert _json_lines(captured.out)[-1]["code"] == "app_content_forbidden"


def test_cli_publish_keyboard_interrupt_is_jsonl_cancellation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._build_publish_dependencies",
        lambda: SimpleNamespace(
            credentials=object(),
            identity_hub=object(),
            publish_hub=object(),
        ),
        raising=False,
    )

    def cancel_publish(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "watcherobot.cli.publish_application",
        cancel_publish,
        raising=False,
    )

    exit_code = main(["app", "publish", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "error",
            "ok": False,
            "code": "operation_cancelled",
            "message": "Application 发布已取消",
        }
    ]


def test_default_publish_dependencies_use_real_hub_adapter() -> None:
    from watcherobot.cli import _build_publish_dependencies

    dependencies = _build_publish_dependencies()

    assert isinstance(dependencies.publish_hub, HuggingFacePublishHubClient)
