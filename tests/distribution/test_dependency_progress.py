"""Exercise real subprocess pipes; no network or host package installation."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from watcherobot.distribution.dependency_progress import run_dependency_install


def run(tmp_path, source, events, **kwargs):
    return run_dependency_install(
        [sys.executable, "-u", "-c", source],
        cwd=tmp_path,
        env=None,
        events=SimpleNamespace(emit=events.append),
        **kwargs,
    )


def test_reports_dependency_activity_before_process_exits_and_redacts_output(tmp_path):
    events = []

    class Sink(list):
        def append(self, event):
            if event.stage == "preparing_dependencies":
                (tmp_path / "activity-seen").write_text("read before child exit")
            events.append(event)

    result = run(
        tmp_path,
        """
import sys,time
from pathlib import Path
print('Resolved 5 packages in 1s',file=sys.stderr,flush=True)
deadline=time.monotonic()+5
while not Path('activity-seen').exists():
    if time.monotonic()>deadline:sys.exit(8)
    time.sleep(0.01)
print('Downloading secret-package (1.2MiB)',file=sys.stderr,flush=True)
print(' Downloaded secret-package',file=sys.stderr,flush=True)
print('Prepared 5 packages in 1s',file=sys.stderr,flush=True)
print('Installed 5 packages in 1s',file=sys.stderr,flush=True)
print('https://user:password@example.invalid/private',file=sys.stderr)
print('ok')
""",
        Sink(),
    )
    assert result.returncode == 0 and result.stdout == b"ok\n"
    assert (tmp_path / "activity-seen").exists()
    assert [e.stage for e in events] == [
        "resolving_dependencies",
        "preparing_dependencies",
        "downloading_dependencies",
        "downloading_dependencies",
        "installing_dependency_files",
        "installing_dependency_files",
    ]
    assert events[-1].data["completed_items"] == 5
    assert events[-1].data["total_items"] == 5
    assert "secret-package" not in str(events) and "password" not in str(events)


def test_unknown_output_does_not_invent_progress_and_crlf_is_supported(tmp_path):
    events = []
    run(
        tmp_path,
        "import sys;sys.stderr.buffer.write(b'warning: something\\r\\nResolved 1 package in 1s\\r\\nAudited 1 package in 1s\\r\\n')",
        events,
    )
    assert events[0].stage == "resolving_dependencies"
    assert all("something" not in str(e) for e in events)
    assert events[-1].data["stage_completed"] == 3
    assert events[-1].data["stage_total"] == 3


def test_streaming_bounds_output_and_kills_timed_out_process(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run(tmp_path, "import time;time.sleep(10)", [], timeout=0.1)
    with pytest.raises(ValueError, match="output"):
        run(
            tmp_path, "import sys;sys.stdout.write('x'*10000)", [], max_output_bytes=128
        )


def test_drains_both_pipes_and_preserves_nonzero_exit(tmp_path):
    result = run(
        tmp_path,
        "import sys;sys.stdout.write('x'*100000);sys.stderr.write('y'*100000);sys.exit(7)",
        [],
    )
    assert result.returncode == 7
    assert len(result.stdout) == len(result.stderr) == 100000
