from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

import watcherobot.distribution.install as install_module
from watcherobot.distribution.events import ErrorCode
from watcherobot.distribution.install import (
    ApplicationEnvironmentCommand,
    ApplicationEnvironmentOutput,
    ApplicationInstallError,
    SystemApplicationEnvironmentRunner,
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
                        {"name": "watcherobot", "version": "0.1.1a3"},
                        {"name": "requests", "version": "2.32.0"},
                    ]
                ).encode()
            )
        if command.stage == "freezing_dependencies":
            return ApplicationEnvironmentOutput(
                stdout=b"watcherobot==0.1.1a3\nrequests==2.32.0\n"
            )
        return ApplicationEnvironmentOutput()


def test_runtime_publication_retries_one_transient_filesystem_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "published-source"
    _write_source(source)
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    real_copytree = install_module.shutil.copytree
    runtime_copy_attempts = 0

    def flaky_copytree(*args, **kwargs):
        nonlocal runtime_copy_attempts
        if Path(args[0]) == runtime:
            runtime_copy_attempts += 1
            if runtime_copy_attempts == 1:
                raise OSError("temporary Windows file lock")
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(install_module.shutil, "copytree", flaky_copytree)
    monkeypatch.setattr(install_module.time, "sleep", lambda _: None)

    installed = install_application(
        space_id=SPACE_ID,
        commit=COMMIT,
        store_root=tmp_path / "application-store",
        runtime_root=runtime,
        hub=FakeHub(source),
        environment_runner=FakeEnvironmentRunner(),
    )

    assert installed.application_id == "com.example.demo"
    assert runtime_copy_attempts == 2


def test_install_allows_an_application_for_another_host_platform(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "published-source"
    _write_source(source, supported_host_platforms=["windows"])
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    # The former host-platform gate consulted this symbol. Installation must
    # remain possible after Desktop has shown and received confirmation for
    # this mismatch.
    monkeypatch.setattr(
        install_module,
        "current_host_platform",
        lambda: "macos",
        raising=False,
    )

    installed = install_application(
        space_id=SPACE_ID,
        commit=COMMIT,
        store_root=tmp_path / "application-store",
        runtime_root=runtime,
        hub=FakeHub(source),
        environment_runner=FakeEnvironmentRunner(),
    )

    assert installed.application_id == "com.example.demo"


def test_environment_runner_retries_one_transient_nonzero_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    attempts = 0

    def flaky_run(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return SimpleNamespace(
            returncode=1 if attempts == 1 else 0,
            stdout=b"",
            stderr=b"temporary file lock" if attempts == 1 else b"",
        )

    monkeypatch.setattr(install_module.subprocess, "run", flaky_run)
    monkeypatch.setattr(install_module.time, "sleep", lambda _: None)
    command = ApplicationEnvironmentCommand(
        stage="installing_dependencies",
        executable=tmp_path / "uv.exe",
        arguments=("pip", "install"),
        current_dir=tmp_path,
        environment_root=tmp_path / ".venv",
    )

    output = SystemApplicationEnvironmentRunner().run(command)

    assert output == ApplicationEnvironmentOutput()
    assert attempts == 2


def test_invalid_cached_runtime_is_archived_and_rebuilt(tmp_path: Path) -> None:
    source = tmp_path / "published-source"
    _write_source(source)
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    store_root = tmp_path / "application-store"
    stale_runtime = store_root / "runtime"
    stale_runtime.mkdir(parents=True)
    stale_runtime.joinpath("invalid.txt").write_text("stale", encoding="utf-8")

    installed = install_application(
        space_id=SPACE_ID,
        commit=COMMIT,
        store_root=store_root,
        runtime_root=runtime,
        hub=FakeHub(source),
        environment_runner=FakeEnvironmentRunner(),
    )

    archived_runtimes = list(store_root.joinpath("trash").glob("*-runtime"))
    assert installed.application_id == "com.example.demo"
    assert store_root.joinpath("runtime/runtime.json").is_file()
    assert len(archived_runtimes) == 1
    assert archived_runtimes[0].joinpath("invalid.txt").read_text(encoding="utf-8") == "stale"


@pytest.mark.parametrize(
    ("damage", "expected_code", "expected_message"),
    [
        (
            lambda root: root.joinpath("runtime.json").unlink(),
            ErrorCode.RUNTIME_RESOURCES_MISSING,
            "Application Runtime resources are missing",
        ),
        (
            lambda root: root.joinpath("runtime.json").write_text("not-json", encoding="utf-8"),
            ErrorCode.RUNTIME_MANIFEST_INVALID,
            "Application Runtime manifest is invalid",
        ),
        (
            lambda root: root.joinpath("uv.exe" if platform.system() == "Windows" else "uv").unlink(),
            ErrorCode.RUNTIME_RESOURCES_MISSING,
            "Application Runtime resources are missing",
        ),
        (
            lambda root: root.joinpath(
                "python/python.exe" if platform.system() == "Windows" else "python/bin/python"
            ).write_bytes(b"modified-python"),
            ErrorCode.RUNTIME_PYTHON_INTEGRITY_FAILED,
            "Application Runtime Python integrity verification failed",
        ),
        (
            lambda root: root.joinpath(
                "uv.exe" if platform.system() == "Windows" else "uv"
            ).write_bytes(b"modified-uv"),
            ErrorCode.RUNTIME_UV_INTEGRITY_FAILED,
            "Application Runtime uv integrity verification failed",
        ),
        (
            lambda root: root.joinpath(
                "wheels/watcherobot-0.1.1a3-py3-none-any.whl"
            ).write_bytes(b"modified-wheel"),
            ErrorCode.RUNTIME_SDK_WHEEL_INTEGRITY_FAILED,
            "Application Runtime SDK wheel integrity verification failed",
        ),
    ],
)
def test_source_runtime_failures_have_stable_sanitized_errors(
    tmp_path: Path,
    damage,
    expected_code: ErrorCode,
    expected_message: str,
) -> None:
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    damage(runtime)

    with pytest.raises(ApplicationInstallError) as captured:
        install_module._load_runtime(runtime)

    assert captured.value.code is expected_code
    assert str(captured.value) == expected_message
    assert str(tmp_path) not in str(captured.value)


def test_runtime_root_resolution_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    original_resolve = Path.resolve

    def fail_runtime_root(path: Path, *args, **kwargs):
        if path == runtime:
            raise RuntimeError(f"symlink loop at {tmp_path}")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_runtime_root)

    with pytest.raises(ApplicationInstallError) as captured:
        install_module._load_runtime(runtime)

    assert captured.value.code is ErrorCode.RUNTIME_RESOURCES_MISSING
    assert str(captured.value) == "Application Runtime resources are missing"
    assert str(tmp_path) not in str(captured.value)


def test_runtime_manifest_path_resolution_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime-source"
    _write_runtime(runtime)
    monkeypatch.setattr(
        install_module,
        "_manifest_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"symlink loop at {tmp_path}")
        ),
    )

    with pytest.raises(ApplicationInstallError) as captured:
        install_module._load_runtime(runtime)

    assert captured.value.code is ErrorCode.RUNTIME_MANIFEST_INVALID
    assert str(captured.value) == "Application Runtime manifest is invalid"
    assert str(tmp_path) not in str(captured.value)


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
    assert record["runtime"]["watcherobot_version"] == "0.1.1a3"
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
        "executable": str(
            installed.application_root
            / ".venv"
            / (
                "Scripts/python.exe"
                if platform.system() == "Windows"
                else "bin/python"
            )
        ),
    }
    assert applications[0].to_dict()["application_directory"] == str(
        installed.application_root / "source"
    )

    removed = uninstall_application(store_root=store_root, application_id="com.example.demo")

    assert not installed.application_root.exists()
    assert removed.trash_root.is_dir()
    assert list_installed_applications(store_root) == ()


def _write_source(
    root: Path,
    *,
    supported_host_platforms: list[str] | None = None,
) -> None:
    root.mkdir(parents=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "com.example.demo",
                "name": "Demo",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": ["requests>=2.32,<3"],
                "supported_host_platforms": supported_host_platforms
                or ["windows", "macos"],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")


def _write_runtime(root: Path) -> None:
    python = root / ("python/python.exe" if platform.system() == "Windows" else "python/bin/python")
    uv = root / ("uv.exe" if platform.system() == "Windows" else "uv")
    wheel = root / "wheels/watcherobot-0.1.1a3-py3-none-any.whl"
    python.parent.mkdir(parents=True)
    wheel.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    uv.write_bytes(b"uv")
    wheel.write_bytes(b"wheel")
    runtime = {
        "schema_version": 1,
        "runtime_id": f"{_platform_name()}-python-3.12.13-watcherobot-0.1.1a3",
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
            "version": "0.1.1a3",
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
