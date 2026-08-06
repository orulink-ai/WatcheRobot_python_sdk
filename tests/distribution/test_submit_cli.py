from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.submit import SubmitError, SubmitResult


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
COMMIT = "a" * 40
PR_URL = "https://huggingface.co/datasets/catalog/discussions/7"


def _result() -> SubmitResult:
    return SubmitResult(
        space_id=SPACE_ID,
        commit=COMMIT,
        source_url=(
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
        ),
        pr_url=PR_URL,
        pr_status="pending",
    )


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def _install_success(monkeypatch):
    dependencies = SimpleNamespace(
        credentials=object(),
        identity_hub=object(),
        publish_hub=object(),
    )
    calls: list[tuple[Path, str | None]] = []

    def fake_submit(application_dir: Path, **kwargs):
        calls.append((application_dir, kwargs["commit"]))
        assert kwargs["credentials"] is dependencies.credentials
        assert kwargs["identity_hub"] is dependencies.identity_hub
        assert kwargs["publish_hub"] is dependencies.publish_hub
        kwargs["events"].emit(
            ProgressEvent(
                stage="updating_catalog",
                message="Preparing the official marketplace submission",
                data={"space_id": SPACE_ID, "commit": COMMIT},
            )
        )
        return _result()

    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_publish_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.submit_application",
        fake_submit,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("app submit must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return calls


def test_cli_submit_jsonl_creates_catalog_request_without_publishing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = _install_success(monkeypatch)

    exit_code = main(
        ["app", "submit", str(tmp_path), "--commit", COMMIT, "--jsonl"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert calls == [(tmp_path, COMMIT)]
    assert _json_lines(captured.out) == [
        {
            "type": "progress",
            "stage": "updating_catalog",
            "message": "Preparing the official marketplace submission",
            "data": {"space_id": SPACE_ID, "commit": COMMIT},
        },
        {"type": "result", "ok": True, "data": _result().to_dict()},
    ]


def test_cli_submit_human_output_shows_review_status(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "submit", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == "Preparing the official marketplace submission\n"
    assert "Catalog submission ready" in captured.out
    assert SPACE_ID in captured.out
    assert PR_URL in captured.out
    assert "Pending review" in captured.out


def test_cli_submit_jsonl_preserves_catalog_conflict_details(
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

    def fail_submit(*args, **kwargs):
        raise SubmitError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "This Application already has a pending marketplace PR",
            details={"space_id": SPACE_ID, "commit": COMMIT, "pr_url": PR_URL},
        )

    monkeypatch.setattr(
        "watcherobot.distribution.cli.submit_application",
        fail_submit,
        raising=False,
    )

    exit_code = main(["app", "submit", str(tmp_path), "--jsonl"])

    assert exit_code == 4
    assert _json_lines(capsys.readouterr().out)[0] == {
        "type": "error",
        "ok": False,
        "code": "catalog_pr_conflict",
        "message": "This Application already has a pending marketplace PR",
        "details": {"space_id": SPACE_ID, "commit": COMMIT, "pr_url": PR_URL},
    }
