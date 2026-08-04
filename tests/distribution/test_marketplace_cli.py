from __future__ import annotations

import json
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.hf_marketplace import (
    HuggingFaceMarketplaceHubClient,
)
from watcherobot.distribution.marketplace import (
    MarketplaceError,
    OfficialMarketplace,
)


CATALOG_COMMIT = "c" * 40


def _marketplace() -> OfficialMarketplace:
    return OfficialMarketplace(
        catalog_commit=CATALOG_COMMIT,
        applications=(),
    )


def _install_success(monkeypatch):
    dependencies = SimpleNamespace(hub=object())
    calls: list[dict[str, object]] = []

    def fake_load(**kwargs):
        calls.append(kwargs)
        assert kwargs["hub"] is dependencies.hub
        kwargs["events"].emit(
            ProgressEvent(
                stage="fetching_catalog",
                message="正在获取官方 Application 名单",
            )
        )
        return _marketplace()

    monkeypatch.setattr(
        "watcherobot.cli._build_marketplace_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.cli.load_official_marketplace",
        fake_load,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("app marketplace must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return calls, dependencies


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def test_cli_marketplace_jsonl_reuses_public_service_without_daemon(
    monkeypatch,
    capsys,
) -> None:
    calls, dependencies = _install_success(monkeypatch)

    exit_code = main(["app", "marketplace", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert len(calls) == 1
    assert set(calls[0]) == {"hub", "events"}
    assert calls[0]["hub"] is dependencies.hub
    assert _json_lines(captured.out) == [
        {
            "type": "progress",
            "stage": "fetching_catalog",
            "message": "正在获取官方 Application 名单",
        },
        {"type": "result", "ok": True, "data": _marketplace().to_dict()},
    ]


def test_cli_marketplace_human_output_shows_catalog_commit_and_count(
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "marketplace"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert CATALOG_COMMIT in captured.out
    assert "0" in captured.out


def test_cli_marketplace_jsonl_maps_stable_marketplace_error(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._build_marketplace_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def fail_load(**kwargs):
        raise MarketplaceError(
            ErrorCode.CATALOG_INVALID,
            "官方 Application 名单结构无效",
            details={"catalog_commit": CATALOG_COMMIT},
        )

    monkeypatch.setattr(
        "watcherobot.cli.load_official_marketplace",
        fail_load,
        raising=False,
    )

    exit_code = main(["app", "marketplace", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "error",
            "ok": False,
            "code": "catalog_invalid",
            "message": "官方 Application 名单结构无效",
            "details": {"catalog_commit": CATALOG_COMMIT},
        }
    ]


def test_cli_marketplace_keyboard_interrupt_is_jsonl_cancellation(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._build_marketplace_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def cancel_load(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "watcherobot.cli.load_official_marketplace",
        cancel_load,
        raising=False,
    )

    exit_code = main(["app", "marketplace", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.err == ""
    assert _json_lines(captured.out)[0]["code"] == "operation_cancelled"


def test_default_marketplace_dependencies_use_public_hub_adapter() -> None:
    from watcherobot.cli import _build_marketplace_dependencies

    dependencies = _build_marketplace_dependencies()

    assert isinstance(dependencies.hub, HuggingFaceMarketplaceHubClient)
