from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from watcherobot.runtime.daemon.application.launcher import (
    ApplicationLaunchError,
    ApplicationLauncher,
    ApplicationLauncherKind,
)


def _write_application(root: Path, *, app_id: str) -> Path:
    root.mkdir(parents=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": app_id,
                "name": "Launcher Test",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('app')\n", encoding="utf-8")
    return root.resolve()


def _write_executable(root: Path, name: str) -> Path:
    executable = root / name
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"launcher")
    if os.name != "nt":
        executable.chmod(0o755)
    return executable.resolve()


def _python_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def _bundled_name() -> str:
    return "watcher-default-app.exe" if os.name == "nt" else "watcher-default-app"


def test_python_launcher_builds_only_the_fixed_app_entrypoint(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "application-store"
    bundled_root = tmp_path / "resources"
    application_dir = _write_application(
        tmp_path / "developer-source",
        app_id="com.example.demo",
    )
    executable = _write_executable(
        managed_root / "apps" / "com.example.demo" / ".venv" / "bin",
        _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=bundled_root,
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="python",
        executable=executable,
    )

    assert spec.app_id == "com.example.demo"
    assert spec.kind is ApplicationLauncherKind.PYTHON
    assert spec.application_dir == application_dir
    assert spec.executable == executable
    assert spec.command == (executable, application_dir / "app.py")


def test_python_launcher_can_start_the_default_application_from_source(
    tmp_path: Path,
) -> None:
    """The official source tree must remain runnable without Desktop."""

    managed_root = tmp_path / "application-store"
    application_dir = _write_application(
        tmp_path / "watcher-default-source",
        app_id="watcher_default",
    )
    executable = _write_executable(
        managed_root / "development" / "python",
        _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=tmp_path / "resources",
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="python",
        executable=executable,
    )

    assert spec.app_id == "watcher_default"
    assert spec.kind is ApplicationLauncherKind.PYTHON
    assert spec.command == (executable, application_dir / "app.py")


def test_source_default_launcher_accepts_only_the_explicit_trusted_pair(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "application-store"
    application_dir = _write_application(
        tmp_path / "workspace" / "WatcheRobot_server",
        app_id="watcher_default",
    )
    executable = _write_executable(
        tmp_path / "workspace" / ".runtime" / "venv" / "bin",
        _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=tmp_path / "resources",
        source_default_application_root=application_dir,
        source_default_launcher_executable=executable,
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="python",
        executable=executable,
    )

    assert spec.app_id == "watcher_default"
    assert spec.executable == executable
    assert spec.command == (executable, application_dir / "app.py")

    other_python = _write_executable(
        tmp_path / "workspace" / ".runtime" / "other" / "bin",
        _python_name(),
    )
    with pytest.raises(ApplicationLaunchError, match="trusted source default"):
        launcher.build_spec(
            application_dir=application_dir,
            kind="python",
            executable=other_python,
        )

    other_application_dir = _write_application(
        tmp_path / "workspace" / "other-server",
        app_id="watcher_default",
    )
    with pytest.raises(ApplicationLaunchError, match="trusted source default"):
        launcher.build_spec(
            application_dir=other_application_dir,
            kind="python",
            executable=executable,
        )


def test_source_default_launcher_configuration_must_be_complete(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be configured together"):
        ApplicationLauncher(
            managed_app_root=tmp_path / "application-store",
            bundled_resource_root=tmp_path / "resources",
            source_default_application_root=tmp_path / "server",
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenvs use Python symlinks")
def test_python_launcher_preserves_virtualenv_symlink_for_execution(
    tmp_path: Path,
) -> None:
    """Validation may resolve the interpreter, but execution must retain venv semantics."""

    application_dir = _write_application(
        tmp_path / "watcher-default-source",
        app_id="watcher_default",
    )
    base_python = _write_executable(tmp_path / "python-runtime", "python3.14")
    virtualenv_python = tmp_path / "server-venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(base_python)
    launcher = ApplicationLauncher(
        managed_app_root=base_python.parent,
        bundled_resource_root=tmp_path / "resources",
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="python",
        executable=virtualenv_python,
    )

    assert spec.executable == virtualenv_python
    assert spec.command == (virtualenv_python, application_dir / "app.py")


def test_windows_python_launcher_uses_pythonw_for_the_fixed_entrypoint(
    tmp_path: Path,
) -> None:
    """Avoid the Windows venv redirector creating a second console window."""

    managed_root = tmp_path / "application-store"
    application_dir = _write_application(
        tmp_path / "developer-source",
        app_id="com.example.demo",
    )
    executable_dir = managed_root / "apps" / "com.example.demo" / ".venv" / "Scripts"
    executable = _write_executable(executable_dir, "python.exe")
    pythonw = _write_executable(executable_dir, "pythonw.exe")
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=tmp_path / "resources",
        is_windows=True,
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="python",
        executable=executable,
    )

    assert spec.command == (pythonw, application_dir / "app.py")


def test_windows_pythonw_must_remain_inside_the_controlled_root(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "application-store"
    application_dir = _write_application(
        tmp_path / "developer-source",
        app_id="com.example.demo",
    )
    executable_dir = (
        managed_root
        / "apps"
        / "com.example.demo"
        / ".venv"
        / "Scripts"
    )
    executable = _write_executable(executable_dir, "python.exe")
    outside_pythonw = _write_executable(tmp_path / "outside", "pythonw.exe")
    pythonw = executable.with_name("pythonw.exe")
    try:
        pythonw.symlink_to(outside_pythonw)
    except OSError as exc:
        pytest.skip(f"symlink is unavailable: {exc}")

    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=tmp_path / "resources",
        is_windows=True,
    )

    with pytest.raises(ApplicationLaunchError, match="controlled root"):
        launcher.build_spec(
            application_dir=application_dir,
            kind="python",
            executable=executable,
        )


def test_bundled_launcher_is_reserved_for_the_default_application(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "application-store"
    bundled_root = tmp_path / "resources"
    application_dir = _write_application(
        bundled_root / "default-application",
        app_id="watcher_default",
    )
    executable = _write_executable(bundled_root, _bundled_name())
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=bundled_root,
    )

    spec = launcher.build_spec(
        application_dir=application_dir,
        kind="bundled",
        executable=executable,
    )

    assert spec.kind is ApplicationLauncherKind.BUNDLED
    assert spec.command == (executable,)


@pytest.mark.parametrize(
    ("kind", "app_id"),
    [
        ("bundled", "com.example.third_party"),
    ],
)
def test_launcher_kind_cannot_cross_the_default_application_boundary(
    tmp_path: Path,
    kind: str,
    app_id: str,
) -> None:
    managed_root = tmp_path / "application-store"
    bundled_root = tmp_path / "resources"
    application_dir = _write_application(tmp_path / "source", app_id=app_id)
    executable = _write_executable(
        bundled_root if kind == "bundled" else managed_root,
        _bundled_name() if kind == "bundled" else _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=bundled_root,
    )

    with pytest.raises(ApplicationLaunchError) as captured:
        launcher.build_spec(
            application_dir=application_dir,
            kind=kind,
            executable=executable,
        )

    assert captured.value.code == "invalid_application_launcher"


@pytest.mark.parametrize("kind", ["python", "bundled"])
def test_launcher_rejects_executable_outside_its_controlled_root(
    tmp_path: Path,
    kind: str,
) -> None:
    managed_root = tmp_path / "application-store"
    bundled_root = tmp_path / "resources"
    app_id = "watcher_default" if kind == "bundled" else "com.example.demo"
    application_dir = _write_application(tmp_path / "source", app_id=app_id)
    executable = _write_executable(
        tmp_path / "unmanaged",
        _bundled_name() if kind == "bundled" else _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=bundled_root,
    )

    with pytest.raises(ApplicationLaunchError, match="controlled root"):
        launcher.build_spec(
            application_dir=application_dir,
            kind=kind,
            executable=executable,
        )


@pytest.mark.parametrize(
    ("kind", "wrong_name"),
    [("python", "arbitrary.exe"), ("bundled", "other-app.exe")],
)
def test_launcher_rejects_arbitrary_executable_names(
    tmp_path: Path,
    kind: str,
    wrong_name: str,
) -> None:
    managed_root = tmp_path / "application-store"
    bundled_root = tmp_path / "resources"
    app_id = "watcher_default" if kind == "bundled" else "com.example.demo"
    application_dir = _write_application(tmp_path / "source", app_id=app_id)
    executable = _write_executable(
        bundled_root if kind == "bundled" else managed_root,
        wrong_name,
    )
    launcher = ApplicationLauncher(
        managed_app_root=managed_root,
        bundled_resource_root=bundled_root,
    )

    with pytest.raises(ApplicationLaunchError, match="executable"):
        launcher.build_spec(
            application_dir=application_dir,
            kind=kind,
            executable=executable,
        )


def test_launcher_rejects_relative_or_missing_executable(tmp_path: Path) -> None:
    application_dir = _write_application(
        tmp_path / "source",
        app_id="com.example.demo",
    )
    launcher = ApplicationLauncher(
        managed_app_root=tmp_path / "application-store",
        bundled_resource_root=tmp_path / "resources",
    )

    for executable in (Path(_python_name()), tmp_path / "missing" / _python_name()):
        with pytest.raises(ApplicationLaunchError):
            launcher.build_spec(
                application_dir=application_dir,
                kind="python",
                executable=executable,
            )


def test_launcher_rejects_unknown_kind_before_process_execution(
    tmp_path: Path,
) -> None:
    application_dir = _write_application(
        tmp_path / "source",
        app_id="com.example.demo",
    )
    executable = _write_executable(
        tmp_path / "application-store",
        _python_name(),
    )
    launcher = ApplicationLauncher(
        managed_app_root=tmp_path / "application-store",
        bundled_resource_root=tmp_path / "resources",
    )

    with pytest.raises(ApplicationLaunchError, match="kind"):
        launcher.build_spec(
            application_dir=application_dir,
            kind="shell",
            executable=executable,
        )
