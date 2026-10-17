"""Run the host entry and verify that both public platform wrappers are present."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name,core,arguments",
    [
        ("run-sdk", "run_sdk.py", ["--describe-runtime"]),
        (
            "verify-sdk-management-release",
            "verify_sdk_management_release.py",
            ["--help"],
        ),
        ("prepare-sdk-ota-release", "prepare_sdk_ota_release.py", ["--help"]),
        (
            "publish-sdk-ota-release",
            "publish_sdk_ota_release.py",
            [
                "--validate-inputs",
                "--sdk-tag",
                "v0.1.13",
                "--catalog-version",
                "0.1.13",
            ],
        ),
    ],
)
def test_paired_scripts_share_core_and_forward_arguments(name, core, arguments):
    shell = ROOT / "tools" / f"{name}.sh"
    powershell = ROOT / "tools" / f"{name}.ps1"
    assert core in shell.read_text() and core in powershell.read_text()
    assert '"$@"' in shell.read_text() and "@args" in powershell.read_text()
    entry = (
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(powershell),
        ]
        if os.name == "nt"
        else ["sh", str(shell)]
    )
    result = subprocess.run(
        [*entry, *arguments],
        env=dict(os.environ, WATCHER_SDK_PYTHON=sys.executable),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_release_input_fails_before_publication():
    from tools.publish_sdk_ota_release import main

    with pytest.raises(ValueError):
        main(
            [
                "--validate-inputs",
                "--sdk-tag=--not-a-tag",
                "--catalog-version",
                "0.1.13",
            ]
        )
