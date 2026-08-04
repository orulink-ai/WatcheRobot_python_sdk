from __future__ import annotations

import subprocess
import sys

import pytest

from watcherobot.distribution.cli import build_parser


@pytest.mark.parametrize(
    "argv",
    [
        ["daemon", "start"],
        ["bluetooth", "scan"],
        ["app", "run", "."],
        ["app", "install", "demo.wapp"],
    ],
)
def test_distribution_parser_rejects_runtime_commands(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(argv)

    assert error.value.code == 2


def test_distribution_entrypoint_does_not_import_runtime_process_modules() -> None:
    script = """
import sys
import watcherobot.distribution.cli

forbidden = {
    'watcherobot.cli',
    'watcherobot.runtime.daemon.instance',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit('Distribution CLI imported runtime modules: ' + ', '.join(loaded))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
