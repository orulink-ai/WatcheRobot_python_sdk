from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

from watcherobot.distribution.install import (
    ApplicationEnvironmentCommand,
    ApplicationEnvironmentOutput,
    install_application,
    list_installed_applications,
    uninstall_application,
)
from watcherobot.distribution.ports import RepositoryRevision


SPACE_ID = "developer/WatcherRobot-com.example.demo"
COMMIT = "a" * 40


class FakeHub:
    def __init__(self, source: Path) -> None:
        self.source = source

    def download_space_snapshot(
        self,
        *,
        space_id: str,
        commit: str,
        target: Path,
    ) -> RepositoryRevision:
        assert space_id == SPACE_ID
        assert commit == COMMIT
        for source in self.source.rglob("*"):
            relative = source.relative_to(self.source)
            destination = target / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
        return RepositoryRevision(
            commit=COMMIT,
            url=f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}",
        )


class FakeEnvironmentRunner:
    def __init__(self) -> None:
        self.commands: list[ApplicationEnvironmentCommand] = []

    def run(self, command: ApplicationEnvironmentCommand) -> ApplicationEnvironmentOutput:
        self.commands.append(command)
        if command.stage == "creating_environment":
            python = command.environment_root / (
                "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
            )
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"python")
        if command.stage == "listing_dependencies":
            return ApplicationEnvironmentOutput(
                stdout=json.dumps(
                    [
                        {"name": "watcherobot", "version": "0.1.1a1"},
                        {"name": "requests", "version": "2.32.0"},
                    ]
                ).encode()
            )
        if command.stage == "freezing_dependencies":
            return ApplicationEnvironmentOutput(
                stdout=b"watcherobot==0.1.1a1\nrequests==2.32.0\n"
            )
        return ApplicationEnvironmentOutput()


def test_install_list_and_uninstall_keep_one_application_root(tmp_path: Path) -> None:
    source = tmp_path / "published-source"
    _write_source(source)
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    store_root = tmp_path / "application-store"
    runner = FakeEnvironmentRunner()

    installed = install_application(
        space_id=SPACE_ID,
        commit=COMMIT,
        store_root=store_root,
        runtime_root=runtime,
        hub=FakeHub(source),
        environment_runner=runner,
    )

    assert installed.application_id == "com.example.demo"
    assert installed.application_root == store_root / "apps/com.example.demo"
    assert installed.application_root.joinpath("source/app.py").is_file()
    assert installed.application_root.joinpath(".venv").is_dir()
    record = json.loads(installed.application_root.joinpath("install.json").read_text())
    assert record["source"]["space_id"] == SPACE_ID
    assert record["source"]["commit"] == COMMIT
    assert record["runtime"]["watcherobot_version"] == "0.1.1a1"
    assert [command.stage for command in runner.commands] == [
        "creating_environment",
        "installing_dependencies",
        "checking_dependencies",
        "compiling_entrypoint",
        "importing_watcherobot",
        "listing_dependencies",
        "freezing_dependencies",
    ]

    applications = list_installed_applications(store_root)

    assert [(item.application_id, item.status) for item in applications] == [
        ("com.example.demo", "installed")
    ]
    assert applications[0].to_dict()["launcher"] == {
        "kind": "python",
        "executable": str(installed.application_root / ".venv/Scripts/python.exe"),
    }
    assert applications[0].to_dict()["application_directory"] == str(
        installed.application_root / "source"
    )

    removed = uninstall_application(store_root=store_root, application_id="com.example.demo")

    assert not installed.application_root.exists()
    assert removed.trash_root.is_dir()
    assert list_installed_applications(store_root) == ()


def _write_source(root: Path) -> None:
    root.mkdir(parents=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "com.example.demo",
                "name": "Demo",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": ["requests>=2.32,<3"],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")


def _write_runtime(root: Path) -> None:
    python = root / ("python/python.exe" if platform.system() == "Windows" else "python/bin/python")
    uv = root / ("uv.exe" if platform.system() == "Windows" else "uv")
    wheel = root / "wheels/watcherobot-0.1.1a1-py3-none-any.whl"
    python.parent.mkdir(parents=True)
    wheel.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    uv.write_bytes(b"uv")
    wheel.write_bytes(b"wheel")
    runtime = {
        "schema_version": 1,
        "runtime_id": f"{_platform_name()}-python-3.12.13-watcherobot-0.1.1a1",
        "platform": _platform_name(),
        "python": {
            "implementation": "cpython",
            "version": "3.12.13",
            "source_url": "https://example.com/python",
            "source_sha256": "0" * 64,
            "root": "python",
            "executable": python.relative_to(root).as_posix(),
            "tree_sha256": _tree_sha256(root / "python"),
        },
        "uv": {
            "version": "0.11.16",
            "source_url": "https://example.com/uv",
            "source_sha256": "1" * 64,
            "executable": uv.relative_to(root).as_posix(),
            "sha256": _sha256(uv),
        },
        "watcherobot": {
            "version": "0.1.1a1",
            "sdk_commit": "2" * 40,
            "wheel": wheel.relative_to(root).as_posix(),
            "sha256": _sha256(wheel),
        },
    }
    root.joinpath("runtime.json").write_text(json.dumps(runtime), encoding="utf-8")


def _platform_name() -> str:
    if platform.system() == "Windows":
        return "win32-x64"
    return "linux-x64"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256(b"watcher-application-runtime-tree-sha256-v1\0")
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "little"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "little"))
        digest.update(payload)
    return digest.hexdigest()
