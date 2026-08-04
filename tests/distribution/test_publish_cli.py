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
            ProgressEvent(stage="checking", message="Validating Application")
        )
        kwargs["events"].emit(
            ProgressEvent(
                stage="resolving_commit",
                message="Resolving the immutable source commit",
                data={"space_id": SPACE_ID},
            )
        )
        return _result()

    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_publish_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.publish_application",
        fake_publish,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("app publish must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return calls


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def test_cli_publish_jsonl_only_returns_fixed_source(
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
            "message": "Validating Application",
        },
        {
            "type": "progress",
            "stage": "resolving_commit",
            "message": "Resolving the immutable source commit",
            "data": {"space_id": SPACE_ID},
        },
        {"type": "result", "ok": True, "data": _result().to_dict()},
    ]
    assert "catalog" not in captured.out.lower()
    assert "token" not in captured.out.lower()


def test_cli_publish_human_output_has_no_catalog_status(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "publish", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == (
        "Validating Application\n"
        "Resolving the immutable source commit\n"
    )
    assert "Application source published" in captured.out
    assert SPACE_ID in captured.out
    assert f"/tree/{COMMIT}" in captured.out
    assert "Catalog" not in captured.out
    assert "discussions/" not in captured.out


def test_cli_publish_maps_remote_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_publish_dependencies",
        lambda: SimpleNamespace(
            credentials=object(),
            identity_hub=object(),
            publish_hub=object(),
        ),
        raising=False,
    )

    def fail_publish(*args, **kwargs):
        raise PublishError(
            ErrorCode.REMOTE_ERROR,
            "Unable to upload Application source",
        )

    monkeypatch.setattr(
        "watcherobot.distribution.cli.publish_application",
        fail_publish,
        raising=False,
    )

    exit_code = main(["app", "publish", str(tmp_path), "--jsonl"])

    assert exit_code == 4
    assert _json_lines(capsys.readouterr().out) == [
        {
            "type": "error",
            "ok": False,
            "code": "remote_error",
            "message": "Unable to upload Application source",
        }
    ]


def test_cli_publish_maps_local_source_error_to_validation_exit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_publish_dependencies",
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
        "watcherobot.distribution.cli.publish_application",
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
        "watcherobot.distribution.cli._build_publish_dependencies",
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
        "watcherobot.distribution.cli.publish_application",
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
            "message": "Application source publishing cancelled",
        }
    ]


def test_default_publish_dependencies_use_real_hub_adapter() -> None:
    from watcherobot.distribution.cli import _build_publish_dependencies

    dependencies = _build_publish_dependencies()

    assert isinstance(dependencies.publish_hub, HuggingFacePublishHubClient)
