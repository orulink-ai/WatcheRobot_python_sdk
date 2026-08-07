from __future__ import annotations

import json
from pathlib import Path

import pytest

from watcherobot.cli import main
from watcherobot.distribution.check import check_application


def write_application(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "com.orulink.demo",
                "name": "Demo",
                "version": "1.2.3",
                "requires_watcherobot": ">=0.1.0a1,<0.2",
                "dependencies": ["httpx>=0.28,<1"],
                "description": "A demo Application",
                "author": "Orulink",
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")


def test_check_application_reuses_manifest_and_returns_structured_data(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)

    result = check_application(
        tmp_path,
        watcherobot_version="0.1.1a2",
    )

    assert result.to_dict() == {
        "schema_version": 1,
        "id": "com.orulink.demo",
        "name": "Demo",
        "version": "1.2.3",
        "requires_watcherobot": ">=0.1.0a1,<0.2",
        "dependencies": ["httpx>=0.28,<1"],
        "description": "A demo Application",
        "author": "Orulink",
        "icon": "",
    }


def test_cli_app_check_jsonl_does_not_start_daemon(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_application(tmp_path)

    def fail_if_called():
        raise AssertionError("app check must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)

    exit_code = main(["app", "check", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert exit_code == 0
    assert captured.err == ""
    assert [event["type"] for event in events] == ["progress", "result"]
    assert events[-1] == {
        "type": "result",
        "ok": True,
        "data": {
            "schema_version": 1,
            "id": "com.orulink.demo",
            "name": "Demo",
            "version": "1.2.3",
            "requires_watcherobot": ">=0.1.0a1,<0.2",
            "dependencies": ["httpx>=0.28,<1"],
            "description": "A demo Application",
            "author": "Orulink",
            "icon": "",
        },
    }


def test_cli_app_check_human_output_uses_same_check_result(
    tmp_path: Path,
    capsys,
) -> None:
    write_application(tmp_path)

    exit_code = main(["app", "check", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == (
        "Application validated\n"
        "\n"
        "Name:             Demo\n"
        "ID:               com.orulink.demo\n"
        "Version:          1.2.3\n"
        "SDK requirement:  >=0.1.0a1,<0.2\n"
        "Dependencies:     httpx>=0.28,<1\n"
        "Author:           Orulink\n"
        "Description:      A demo Application\n"
    )


def test_app_check_allows_local_venv_but_never_treats_it_as_source(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)
    local_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    local_python.parent.mkdir(parents=True)
    local_python.write_text("local", encoding="utf-8")

    result = check_application(tmp_path, watcherobot_version="0.1.1a2")

    assert result.app_id == "com.orulink.demo"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda root: root.joinpath("app.json").unlink(),
            "app_manifest_missing",
        ),
        (
            lambda root: root.joinpath("app.py").unlink(),
            "app_entrypoint_missing",
        ),
        (
            lambda root: _update_manifest(root, unknown="value"),
            "app_manifest_invalid",
        ),
        (
            lambda root: _update_manifest(
                root,
                dependencies=["not a requirement ???"],
            ),
            "app_dependency_invalid",
        ),
        (
            lambda root: _update_manifest(
                root,
                dependencies=[
                    "watcherobot @ https://example.com/alternate.whl"
                ],
            ),
            "app_dependency_invalid",
        ),
        (
            lambda root: _update_manifest(
                root,
                requires_watcherobot=">=9,<10",
            ),
            "app_sdk_incompatible",
        ),
    ],
)
def test_cli_app_check_jsonl_has_stable_validation_errors(
    tmp_path: Path,
    capsys,
    mutate,
    expected_code: str,
) -> None:
    write_application(tmp_path)
    mutate(tmp_path)

    exit_code = main(["app", "check", str(tmp_path), "--jsonl"])

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert exit_code == 2
    assert captured.err == ""
    assert events[-1]["type"] == "error"
    assert events[-1]["ok"] is False
    assert events[-1]["code"] == expected_code
    assert "Traceback" not in captured.out


def _update_manifest(root: Path, **updates: object) -> None:
    path = root / "app.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")
