from __future__ import annotations

import json
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.hf_marketplace import (
    HuggingFaceMarketplaceHubClient,
)
from watcherobot.distribution.marketplace import (
    MarketplaceApplication,
    MarketplaceError,
    OfficialMarketplace,
)


CATALOG_COMMIT = "c" * 40


def _marketplace() -> OfficialMarketplace:
    return OfficialMarketplace(
        catalog_commit=CATALOG_COMMIT,
        applications=(
            MarketplaceApplication(
                space_id="alice/WatcherRobot-com.example.alpha",
                commit="a" * 40,
                source_url=(
                    "https://huggingface.co/spaces/alice/"
                    "WatcherRobot-com.example.alpha/tree/" + "a" * 40
                ),
                schema_version=2,
                app_id="com.example.alpha",
                name="Alpha Robot",
                version="1.2.3",
                requires_watcherobot=">=0.1,<0.2",
                dependencies=("requests>=2.32,<3",),
                description="Alpha description",
                author="Alice",
                icon="",
                compatible=True,
                supported_host_platforms=("windows", "macos"),
                host_compatible=True,
            ),
            MarketplaceApplication(
                space_id="bob/WatcherRobot-com.example.beta",
                commit="b" * 40,
                source_url=(
                    "https://huggingface.co/spaces/bob/"
                    "WatcherRobot-com.example.beta/tree/" + "b" * 40
                ),
                schema_version=2,
                app_id="com.example.beta",
                name="Beta Robot",
                version="2.0.0",
                requires_watcherobot=">=2,<3",
                dependencies=(),
                description="",
                author="Bob",
                icon="icon.png",
                compatible=False,
                supported_host_platforms=("windows",),
                host_compatible=False,
            ),
        ),
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
                message="Loading official Application marketplace",
            )
        )
        return _marketplace()

    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_marketplace_dependencies",
        lambda: dependencies,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.load_official_marketplace",
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
            "message": "Loading official Application marketplace",
        },
        {"type": "result", "ok": True, "data": _marketplace().to_dict()},
    ]


def test_cli_marketplace_human_output_is_a_compact_table(
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "marketplace"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == "Loading official Application marketplace...\n"
    assert captured.out == (
        "Application Marketplace\n"
        f"Catalog commit: {CATALOG_COMMIT}\n"
        "Applications: 2\n"
        "\n"
        "STATUS        VERSION      NAME                     APPLICATION ID\n"
        "------------------------------------------------------------------\n"
        "Compatible    1.2.3        Alpha Robot              com.example.alpha\n"
        "Incompatible  2.0.0        Beta Robot               com.example.beta\n"
    )


def test_cli_marketplace_details_shows_reviewed_source_metadata(
    monkeypatch,
    capsys,
) -> None:
    _install_success(monkeypatch)

    exit_code = main(["app", "marketplace", "--details"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "1. Alpha Robot" in captured.out
    assert "ID:               com.example.alpha" in captured.out
    assert "Compatibility:    Compatible" in captured.out
    assert "Host platforms:" in captured.out
    assert "Windows, macOS" in captured.out
    assert "Dependencies:     requests>=2.32,<3" in captured.out
    assert "Source:           https://huggingface.co/spaces/alice/" in captured.out
    assert "Commit:           " + "a" * 40 in captured.out


def test_cli_marketplace_jsonl_maps_stable_marketplace_error(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_marketplace_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def fail_load(**kwargs):
        raise MarketplaceError(
            ErrorCode.CATALOG_INVALID,
            "The official Application marketplace is invalid",
            details={"catalog_commit": CATALOG_COMMIT},
        )

    monkeypatch.setattr(
        "watcherobot.distribution.cli.load_official_marketplace",
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
            "message": "The official Application marketplace is invalid",
            "details": {"catalog_commit": CATALOG_COMMIT},
        }
    ]


def test_cli_marketplace_keyboard_interrupt_is_jsonl_cancellation(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_marketplace_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def cancel_load(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "watcherobot.distribution.cli.load_official_marketplace",
        cancel_load,
        raising=False,
    )

    exit_code = main(["app", "marketplace", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.err == ""
    assert _json_lines(captured.out)[0]["code"] == "operation_cancelled"


def test_default_marketplace_dependencies_use_public_hub_adapter() -> None:
    from watcherobot.distribution.cli import _build_marketplace_dependencies

    dependencies = _build_marketplace_dependencies()

    assert isinstance(dependencies.hub, HuggingFaceMarketplaceHubClient)
