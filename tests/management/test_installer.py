import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from watcherobot.management.installer import install_environment
from watcherobot.management.releases import Artifact, Release


@pytest.mark.parametrize("source_dependency", [False, True])
def test_installation_is_locked_offline_and_does_not_start_daemon(
    tmp_path: Path, source_dependency
):
    calls = []

    class Runner:
        def run(self, cmd):
            calls.append(cmd)
            if "--check-runtime" in cmd.arguments:
                out = json.dumps(
                    {
                        "sdk_version": "0.1.13",
                        "build_id": "source:abc",
                        "contracts": {
                            "manager": 1,
                            "management": 1,
                            "distribution": 1,
                            "application": 1,
                            "state": 1,
                        },
                    }
                ).encode()
            else:
                out = b""
            return SimpleNamespace(stdout=out, stderr=b"")

    runtime = SimpleNamespace(
        python_executable=tmp_path / "python", uv_executable=tmp_path / "uv"
    )
    wheel = Artifact(
        "watcherobot",
        "0.1.13",
        "watcherobot-0.1.13-py3-none-any.whl",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/a.whl",
        "a" * 64,
        1,
    )
    release = Release(
        "0.1.13",
        "darwin-arm64",
        ">=3.10,<3.13",
        {
            "manager": 1,
            "management": 1,
            "distribution": 1,
            "application": 1,
            "state": 1,
        },
        (wheel,),
        "",
        "b" * 64,
    )
    if source_dependency:
        from dataclasses import replace

        source = Artifact(
            "esptool",
            "5.1.0",
            "esptool-5.1.0.tar.gz",
            "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/source.tar.gz",
            "c" * 64,
            100,
        )
        build = Artifact(
            "setuptools",
            "80.0.0",
            "setuptools-80.0.0-py3-none-any.whl",
            "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/build.whl",
            "d" * 64,
            100,
        )
        release = replace(
            release,
            requirements=(*release.requirements, source),
            build_requirements=(build,),
        )
    target = tmp_path / "environment"
    info = install_environment(
        target,
        runtime,
        release,
        [tmp_path / wheel.filename],
        tmp_path / "cache",
        Runner(),
    )
    assert info["sdk_version"] == "0.1.13"
    install = next(c for c in calls if c.stage == "installing_sdk")
    assert "--offline" in install.arguments and "--require-hashes" in install.arguments
    assert "--no-deps" in install.arguments and "--no-index" in install.arguments
    if source_dependency:
        assert "--no-build-isolation" in install.arguments
        build_install = next(c for c in calls if c.stage == "installing_build_tools")
        assert (
            "--require-hashes" in build_install.arguments
            and "--offline" in build_install.arguments
        )
        assert calls.index(build_install) < calls.index(install)
    assert all("--activate-shared" not in c.arguments for c in calls)
    assert all("--ensure-shared" not in c.arguments for c in calls)


def test_sdk_install_command_can_cancel_and_reaps_its_child(tmp_path):
    import sys
    import time

    import psutil

    from watcherobot.distribution.install import (
        ApplicationEnvironmentCommand,
        SystemApplicationEnvironmentRunner,
    )
    from watcherobot.management.state import ManagerCancelled

    marker = tmp_path / "pid"
    deadline = time.monotonic() + 5

    def checkpoint():
        if marker.exists():
            raise ManagerCancelled("cancelled")
        assert time.monotonic() < deadline

    command = ApplicationEnvironmentCommand(
        "installing_sdk",
        Path(sys.executable),
        (
            "-c",
            "import os,time; from pathlib import Path; Path("
            + repr(str(marker))
            + ").write_text(str(os.getpid())); time.sleep(60)",
        ),
        tmp_path,
        tmp_path,
    )
    with pytest.raises(ManagerCancelled):
        SystemApplicationEnvironmentRunner(checkpoint=checkpoint).run(command)
    assert not psutil.pid_exists(int(marker.read_text()))
