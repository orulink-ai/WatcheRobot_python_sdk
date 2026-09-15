from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from watcherobot.runtime import process_shutdown


def test_unrelated_listener_is_never_selected(monkeypatch):
    process = Mock()
    process.cmdline.return_value = ["python", "unrelated.py"]
    process.exe.return_value = "/bin/python"
    process.net_connections.return_value = [
        SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=8767))
    ]
    monkeypatch.setattr(process_shutdown.psutil, "Process", lambda pid: process)
    with pytest.raises(RuntimeError, match="identity"):
        process_shutdown.capture_runtime(123, 8767)
    process.terminate.assert_not_called()


def test_runtime_must_own_control_listener(monkeypatch):
    process = Mock()
    process.cmdline.return_value = ["python", "-m", "watcherobot.runtime.daemon"]
    process.exe.return_value = "/bin/python"
    process.net_connections.return_value = []
    monkeypatch.setattr(process_shutdown.psutil, "Process", lambda pid: process)
    with pytest.raises(RuntimeError, match="listener"):
        process_shutdown.capture_runtime(123, 8767)


def test_hung_verified_process_is_killed_and_waited(monkeypatch):
    process = Mock()
    wait = Mock(side_effect=[([], [process]), ([process], [])])
    monkeypatch.setattr(process_shutdown.psutil, "wait_procs", wait)
    process_shutdown.force_stop([process])
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert wait.call_count == 2


def test_isolated_launcher_cannot_migrate_default_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    inspect = Mock()
    monkeypatch.setattr(process_shutdown.psutil, "net_connections", inspect)
    assert process_shutdown.stop_legacy_listener() is False
    inspect.assert_not_called()


@pytest.mark.parametrize(
    "identity", [{"instance_group": "isolated"}, {"instance_id": "sha256:foreign"}]
)
def test_legacy_migration_rejects_other_instances(monkeypatch, identity):
    from watcherobot import cli
    from watcherobot.runtime.daemon import instance

    monkeypatch.setattr(
        instance, "default_runtime_instance_root", instance.system_runtime_instance_root
    )
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "8767")
    monkeypatch.setattr(
        process_shutdown.psutil,
        "net_connections",
        lambda **kwargs: [
            SimpleNamespace(pid=123, status="LISTEN", laddr=SimpleNamespace(port=8767))
        ],
    )
    monkeypatch.setattr(
        cli, "_request_json", lambda *args, **kwargs: {"runtime": identity}
    )
    capture = Mock()
    monkeypatch.setattr(process_shutdown, "capture_runtime", capture)
    with pytest.raises(RuntimeError, match="belongs to"):
        process_shutdown.stop_legacy_listener()
    capture.assert_not_called()
