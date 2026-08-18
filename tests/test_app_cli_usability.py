from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from watcherobot import __version__
from watcherobot.cli import build_parser, main
from watcherobot.distribution.cli import build_parser as build_distribution_parser


def _help_for(parser, argv: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as captured:
        parser.parse_args([*argv, "--help"])

    assert captured.value.code == 0
    return capsys.readouterr().out


def test_global_version_prints_installed_sdk_version(capsys) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])

    assert captured.value.code == 0
    assert capsys.readouterr().out == f"watcherobot {__version__}\n"


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

    assert "Create a runnable Application project" in output
    for option in ("--id", "--name", "--author", "--description", "--platform"):
        assert option in output
    assert "defaults from the project directory" in output


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
            "--platform",
            "windows",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith("Application project created\n\n")
    assert f"Directory:  {target.resolve()}" in captured.out
    assert "ID:         com.example.demo" in captured.out
    assert "Next:" in captured.out
    assert f'cd "{target.resolve()}"' in captured.out
    assert "watcherobot app run" in captured.out
    assert target.joinpath("app.json").is_file()
    app_source = target.joinpath("app.py").read_text(encoding="utf-8")
    assert app_source.index("Hello, WatcheRobot!") < app_source.index(
        'app.robot.supports("behavior")'
    )
    assert "Run 'watcherobot robot setup'" in app_source
    assert 'app.robot.behavior.play,\n            "happy"' in app_source
    assert "job.wait, 20.0" in app_source
    assert "SILENT_EXPRESSIONS = (" in app_source
    assert "_shuffled_silent_expressions" in app_source
    assert "app.robot.animation.available_ids" in app_source
    assert "app.robot.animation.play" in app_source
    assert (
        "job.wait,\n                    SILENT_EXPRESSION_TIMEOUT_SECONDS"
        in app_source
    )
    assert "DEMO_BEHAVIOR_SECONDS" not in app_source
    assert "app.robot.lights.play_effect" in app_source
    assert "while True:" in app_source
    assert "watcherobot robot setup" in captured.out
    generated_readme = target.joinpath("README.md").read_text(encoding="utf-8")
    assert "watcherobot robot setup" in generated_readme
    assert "always logs a Hello World success" in generated_readme
    assert (
        "waits for each randomly selected silent expression to"
        in generated_readme
    )


def test_app_init_derives_metadata_without_prompts(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "hello_robot_test"
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("explicit project directory must not prompt")
        ),
    )

    assert main(["app", "init", str(target)]) == 0

    manifest = json.loads(
        target.joinpath("app.json").read_text(encoding="utf-8")
    )
    assert manifest["id"] == "local.hello_robot_test"
    assert manifest["name"] == "Hello Robot Test"
    assert manifest["author"] == "Local Developer"
    assert (
        manifest["description"]
        == "Hello Robot Test WatcheRobot Application."
    )
    assert manifest["supported_host_platforms"] == ["windows", "macos"]
    assert capsys.readouterr().err == ""


def test_app_init_accepts_only_a_directory_in_non_interactive_use(
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

    assert main(["app", "init", str(target)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Application project created" in captured.out
    assert target.is_dir()


def test_app_init_prompts_only_for_directory_when_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "prompted-app"
    prompts: list[str] = []
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
        raising=False,
    )

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return str(target)

    monkeypatch.setattr("builtins.input", answer)

    assert main(["app", "init"]) == 0
    assert prompts == ["Project directory [hello_robot]: "]
    assert json.loads(target.joinpath("app.json").read_text(encoding="utf-8"))[
        "name"
    ] == "Prompted App"


def test_app_init_requires_directory_in_non_interactive_use(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: False,
        raising=False,
    )

    assert main(["app", "init"]) == 2
    assert "project directory" in capsys.readouterr().err.lower()


def test_app_run_defaults_to_current_directory(monkeypatch) -> None:
    applications = []
    monkeypatch.setattr(
        "watcherobot.cli.run_application",
        lambda application: applications.append(application) or 0,
    )

    assert main(["app", "run"]) == 0
    assert applications == [Path(".")]


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
    requests: list[dict[str, object]] = []

    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (SimpleNamespace(control_url="http://runtime"), True),
    )
    def fake_request_json(*args, **kwargs):
        requests.append(kwargs)
        return {
            "application": {
                "current_app": "com.example.demo",
                "state": state,
                "process_id": 123 if state == "running" else None,
            }
        }

    monkeypatch.setattr("watcherobot.cli._request_json", fake_request_json)

    exit_code = main(["app", command])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith(f"{title}\n\n")
    assert "ID:     com.example.demo" in captured.out
    assert f"State:  {state}" in captured.out
    if command == "start":
        assert requests == [{"method": "POST", "timeout": 90.0}]
    else:
        assert requests == [{"method": "POST"}]
