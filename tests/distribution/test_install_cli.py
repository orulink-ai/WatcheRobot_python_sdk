from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.install import (
    ApplicationInstallResult,
    ApplicationUninstallResult,
    InstalledApplication,
)


SPACE_ID = "developer/WatcherRobot-com.example.demo"
COMMIT = "a" * 40


def test_cli_install_jsonl_uses_sdk_store_service_without_daemon(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    store_root = tmp_path / "store"
    runtime_root = tmp_path / "runtime"
    result = ApplicationInstallResult(
        application_id="com.example.demo",
        name="Demo",
        version="1.0.0",
        application_root=store_root / "apps/com.example.demo",
        source_url=f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}",
        commit=COMMIT,
        runtime_id="win32-x64-python-3.12.13-watcherobot-0.1.1a1",
        replaced_existing=False,
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "watcherobot.distribution.cli._build_install_dependencies",
        lambda: SimpleNamespace(hub=object()),
        raising=False,
    )

    def fake_install(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "watcherobot.distribution.cli.install_application",
        fake_install,
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("install started Daemon")),
    )

    assert (
        main(
            [
                "app",
                "install",
                "--space-id",
                SPACE_ID,
                "--commit",
                COMMIT,
                "--store-root",
                str(store_root),
                "--runtime-root",
                str(runtime_root),
                "--jsonl",
            ]
        )
        == 0
    )

    assert calls[0]["hub"] is not None
    assert calls[0]["store_root"] == store_root
    assert calls[0]["runtime_root"] == runtime_root
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"type": "result", "ok": True, "data": result.to_dict()}
    ]


def test_cli_list_and_uninstall_jsonl_use_sdk_store_service(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    store_root = tmp_path / "store"
    application = InstalledApplication(
        application_id="com.example.demo",
        name="Demo",
        version="1.0.0",
        status="installed",
        application_root=store_root / "apps/com.example.demo",
        space_id=SPACE_ID,
        commit=COMMIT,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.list_installed_applications",
        lambda root: (application,),
        raising=False,
    )
    monkeypatch.setattr(
        "watcherobot.distribution.cli.uninstall_application",
        lambda **kwargs: ApplicationUninstallResult(
            application_id=kwargs["application_id"],
            trash_root=store_root / "trash/transaction",
        ),
        raising=False,
    )

    assert main(["app", "list", "--store-root", str(store_root), "--jsonl"]) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {
            "type": "result",
            "ok": True,
            "data": {"applications": [application.to_dict()]},
        }
    ]
    assert (
        main(
            [
                "app",
                "uninstall",
                "--store-root",
                str(store_root),
                "--app-id",
                "com.example.demo",
                "--jsonl",
            ]
        )
        == 0
    )
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {
            "type": "result",
            "ok": True,
            "data": {
                "id": "com.example.demo",
                "trash_root": str(store_root / "trash/transaction"),
            },
        }
    ]
