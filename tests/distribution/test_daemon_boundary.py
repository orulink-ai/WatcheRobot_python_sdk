from __future__ import annotations

import subprocess
import sys


def test_daemon_entrypoint_does_not_import_distribution_modules() -> None:
    script = """
import sys
import watcherobot.runtime.daemon.__main__

loaded = sorted(
    name for name in sys.modules if name.startswith('watcherobot.distribution')
)
if loaded:
    raise SystemExit('Daemon imported distribution modules: ' + ', '.join(loaded))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
