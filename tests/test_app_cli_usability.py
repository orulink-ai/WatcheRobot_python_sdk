from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from watcherobot.cli import build_parser, main
from watcherobot.distribution.cli import build_parser as build_distribution_parser


def _help_for(parser, argv: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as captured:
        parser.parse_args([*argv, "--help"])

    assert captured.value.code == 0
    return capsys.readouterr().out


def test_app_help_describes_the_supported_developer_workflow(capsys) -> None:
    output = _help_for(build_parser(), ["app"], capsys)

    for command in (
        "init",
        "run",
        "run-installed",
        "check",
        "login",
        "logout",
        "publish",
        "submit",
        "marketplace",
        "download",
        "install",
        "list",
        "uninstall",
        "start",
        "stop",
    ):
        assert re.search(rf"(?m)^\s+{command}\s+", output)
    assert not re.search(r"(?m)^\s+select(?:\s+|$)", output)
    assert not re.search(r"(?m)^\s+package(?:\s+|$)", output)
    assert "Typical workflow" in output
    assert "For manual use, omit --jsonl" in output


def test_marketplace_help_separates_human_and_machine_output(capsys) -> None:
    output = _help_for(build_parser(), ["app", "marketplace"], capsys)

    assert "--details" in output
    assert "--jsonl" in output
    assert "Show complete metadata" in output
    assert "Desktop automation" in output


def test_distribution_sidecar_help_contains_only_distribution_commands(
    capsys,
) -> None:
    output = _help_for(build_distribution_parser(), ["app"], capsys)

    for command in (
        "check",
        "login",
        "logout",
        "publish",
        "submit",
        "marketplace",
        "download",
        "install",
        "list",
        "uninstall",
    ):
        assert re.search(rf"(?m)^\s+{command}\s+", output)
    for runtime_command in (
        "init",
        "run",
        "run-installed",
        "start",
        "stop",
    ):
        assert not re.search(rf"(?m)^\s+{runtime_command}(?:\s+|$)", output)


def test_app_init_help_describes_interactive_and_scripted_usage(capsys) -> None:
    output = _help_for(build_parser(), ["app", "init"], capsys)

    assert "Create a publish-ready Application project" in output
    for option in ("--id", "--name", "--author", "--description"):
        assert option in output
    assert "prompts for any metadata option" in output


def test_app_init_creates_project_without_starting_daemon(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "demo"

    def fail_if_called():
        raise AssertionError("app init must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)

    exit_code = main(
        [
            "app",
            "init",
            str(target),
            "--id",
            "com.example.demo",
            "--name",
            "Demo",
            "--author",
            "Example Team",
            "--description",
            "Example Application",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith("Application project created\n\n")
    assert f"Directory:  {target.resolve()}" in captured.out
    assert "ID:         com.example.demo" in captured.out
    assert "Next:" in captured.out
    assert f"watcherobot app check \"{target.resolve()}\"" in captured.out
    assert target.joinpath("app.json").is_file()
    assert "await asyncio.Event().wait()" in target.joinpath("app.py").read_text(
        encoding="utf-8"
    )


def test_app_init_prompts_for_metadata_in_an_interactive_terminal(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "prompted"
    answers = iter(
        [
            "com.example.prompted",
            "Prompted App",
            "Example Team",
            "Created from prompts",
        ]
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main(["app", "init", str(target)]) == 0

    manifest = json.loads(
        target.joinpath("app.json").read_text(encoding="utf-8")
    )
    assert manifest["id"] == "com.example.prompted"
    assert manifest["name"] == "Prompted App"
    assert manifest["author"] == "Example Team"
    assert manifest["description"] == "Created from prompts"
    assert capsys.readouterr().err == ""


def test_app_init_requires_flags_when_input_is_not_interactive(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "non-interactive"
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: False,
        raising=False,
    )

    assert main(["app", "init", str(target), "--name", "Demo"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--id, --author, --description" in captured.err
    assert not target.exists()


@pytest.mark.parametrize(
    ("command", "title", "state"),
    [
        ("start", "Application started", "running"),
        ("stop", "Application stopped", "ended"),
    ],
)
def test_app_runtime_actions_render_a_readable_summary(
    command: str,
    title: str,
    state: str,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (SimpleNamespace(control_url="http://runtime"), True),
    )
    monkeypatch.setattr(
        "watcherobot.cli._request_json",
        lambda *args, **kwargs: {
            "application": {
                "current_app": "com.example.demo",
                "state": state,
                "process_id": 123 if state == "running" else None,
            }
        },
    )

    exit_code = main(["app", command])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith(f"{title}\n\n")
    assert "ID:     com.example.demo" in captured.out
    assert f"State:  {state}" in captured.out
