from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.check import ApplicationCheckResult
from watcherobot.distribution.download import DownloadError, DownloadResult
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.hf_marketplace import (
    HuggingFaceMarketplaceHubClient,
)


SPACE_ID = "alice/WatcherRobot-com.example.demo"
COMMIT = "a" * 40


def _result(target: Path) -> DownloadResult:
    return DownloadResult(
        space_id=SPACE_ID,
        commit=COMMIT,
        source_url=(
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
        ),
        target=target.resolve(),
        application=ApplicationCheckResult(
            schema_version=1,
            app_id="com.example.demo",
            name="Demo",
            version="1.2.3",
            requires_watcherobot=">=1.0,<2.0",
            dependencies=(),
            description="Demo",
            author="Developer",
            icon="",
        ),
    )


def _install_success(monkeypatch, target: Path):
    dependencies = SimpleNamespace(hub=object())
    calls: list[dict[str, object]] = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        assert kwargs["hub"] is dependencies.hub
        kwargs["events"].emit(
            ProgressEvent(
                stage="downloading_snapshot",
                message="Downloading immutable Application source",
                data={"space_id": SPACE_ID, "commit": COMMIT},
            )
        )
        return _result(target)

    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_download_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.download_application_snapshot",
        fake_download,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("app download must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return calls


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def test_cli_download_jsonl_reuses_service_and_never_starts_daemon(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = _install_success(monkeypatch, tmp_path)

    exit_code = main(
        [
            "app",
            "download",
            "--space-id",
            SPACE_ID,
            "--commit",
            COMMIT,
            "--target",
            str(tmp_path),
            "--jsonl",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert calls[0]["space_id"] == SPACE_ID
    assert calls[0]["commit"] == COMMIT
    assert calls[0]["target"] == tmp_path
    assert _json_lines(captured.out) == [
        {
            "type": "progress",
            "stage": "downloading_snapshot",
            "message": "Downloading immutable Application source",
            "data": {"space_id": SPACE_ID, "commit": COMMIT},
        },
        {"type": "result", "ok": True, "data": _result(tmp_path).to_dict()},
    ]


def test_cli_download_human_output_shows_fixed_source_and_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch, tmp_path)

    exit_code = main(
        [
            "app",
            "download",
            "--space-id",
            SPACE_ID,
            "--commit",
            COMMIT,
            "--target",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == "Downloading immutable Application source\n"
    assert "Application downloaded" in captured.out
    assert SPACE_ID in captured.out
    assert COMMIT in captured.out
    assert str(tmp_path.resolve()) in captured.out


def test_cli_download_jsonl_maps_stable_download_error(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_download_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def fail_download(**kwargs):
        raise DownloadError(
            ErrorCode.REMOTE_ERROR,
            "Unable to download immutable Application source",
            details={"space_id": SPACE_ID, "commit": COMMIT},
        )

    monkeypatch.setattr(
        "watcherobot.distribution.cli.download_application_snapshot",
        fail_download,
        raising=False,
    )

    exit_code = main(
        [
            "app",
            "download",
            "--space-id",
            SPACE_ID,
            "--commit",
            COMMIT,
            "--target",
            str(tmp_path),
            "--jsonl",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "error",
            "ok": False,
            "code": "remote_error",
            "message": "Unable to download immutable Application source",
            "details": {"space_id": SPACE_ID, "commit": COMMIT},
        }
    ]


def test_cli_download_keyboard_interrupt_is_jsonl_cancellation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_download_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def cancel_download(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "watcherobot.distribution.cli.download_application_snapshot",
        cancel_download,
        raising=False,
    )

    exit_code = main(
        [
            "app",
            "download",
            "--space-id",
            SPACE_ID,
            "--commit",
            COMMIT,
            "--target",
            str(tmp_path),
            "--jsonl",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.err == ""
    assert _json_lines(captured.out)[0]["code"] == "operation_cancelled"


def test_default_download_dependencies_use_public_hub_adapter() -> None:
    from watcherobot.distribution.cli import _build_download_dependencies

    dependencies = _build_download_dependencies()

    assert isinstance(dependencies.hub, HuggingFaceMarketplaceHubClient)
