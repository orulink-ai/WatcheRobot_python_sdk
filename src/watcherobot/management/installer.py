"""Build a candidate from verified local wheels without disturbing the live SDK."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watcherobot.distribution.install import (
    ApplicationEnvironmentCommand,
    ApplicationEnvironmentRunner,
    SystemApplicationEnvironmentRunner,
)

from .releases import Release
from .state import atomic_json


def environment_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_environment(
    root: Path,
    runtime: Any,
    release: Release,
    wheels: list[Path],
    cache: Path,
    runner: ApplicationEnvironmentRunner | None = None,
    checkpoint: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    python = environment_python(root)
    lock = root / "requirements.lock"
    lock.write_text(
        "".join(
            f"{w.name}=={w.version} --hash=sha256:{w.sha256}\n"
            for w in release.requirements
        ),
        encoding="utf-8",
    )
    uv = runtime.uv_executable
    args = ["--no-config", "--no-python-downloads"]
    commands = [
        (
            "creating_sdk",
            uv,
            [
                *args,
                "venv",
                "--allow-existing",
                "--python",
                str(runtime.python_executable),
                str(root),
            ],
        ),
        (
            "installing_sdk",
            uv,
            [
                *args,
                "pip",
                "install",
                "--python",
                str(python),
                "--offline",
                "--no-index",
                "--no-deps",
                "--require-hashes",
                *(
                    ["--only-binary", ":all:"]
                    if all(w.is_wheel for w in release.requirements)
                    else ["--no-build-isolation"]
                ),
                "--link-mode",
                "copy" if os.name == "nt" else "clone",
                *(
                    arg
                    for directory in sorted({w.parent for w in wheels})
                    for arg in ("--find-links", str(directory))
                ),
                "-r",
                str(lock),
            ],
        ),
        ("checking_sdk", uv, [*args, "pip", "check", "--python", str(python)]),
        (
            "probing_sdk",
            python,
            ["-I", "-m", "watcherobot.runtime.daemon", "--check-runtime"],
        ),
        (
            "probing_distribution",
            python,
            [
                "-I",
                "-c",
                "from watcherobot.distribution.cli import main; raise SystemExit(main(['--help']))",
            ],
        ),
    ]
    if release.build_requirements:
        build_lock = root / "build-requirements.lock"
        build_lock.write_text(
            "".join(
                f"{w.name}=={w.version} --hash=sha256:{w.sha256}\n"
                for w in release.build_requirements
            ),
            encoding="utf-8",
        )
        commands.insert(
            1,
            (
                "installing_build_tools",
                uv,
                [
                    *args,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--offline",
                    "--no-index",
                    "--no-deps",
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    *(
                        arg
                        for directory in sorted({w.parent for w in wheels})
                        for arg in ("--find-links", str(directory))
                    ),
                    "-r",
                    str(build_lock),
                ],
            ),
        )
    executor = runner or SystemApplicationEnvironmentRunner(checkpoint=checkpoint)
    identity: dict[str, Any] | None = None
    for stage, executable, arguments in commands:
        checkpoint()
        output = executor.run(
            ApplicationEnvironmentCommand(
                stage,
                executable,
                tuple(arguments),
                root,
                root,
                (("UV_CACHE_DIR", str(cache)),),
            )
        )
        if stage == "probing_sdk":
            identity = json.loads(output.stdout)
            if (
                not isinstance(identity, dict)
                or identity.get("sdk_version") != release.version
                or identity.get("contracts") != release.contracts
                or not identity.get("build_id")
            ):
                raise ValueError(
                    "Installed SDK differs from the verified release contract"
                )
    if identity is None:
        raise ValueError("SDK candidate did not pass runtime verification")
    atomic_json(
        root / "sdk-environment.json",
        {
            "version": release.version,
            "identity": release.identity,
            "python": str(python),
            "build_id": identity["build_id"],
            "contracts": release.contracts,
        },
    )
    return identity
