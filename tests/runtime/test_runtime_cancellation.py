import pytest

from watcherobot.runtime import repository
from watcherobot.runtime.manager import RuntimeActivationCancelled


def test_validation_can_be_cancelled_while_candidate_is_loading(tmp_path, monkeypatch):
    import sys
    import threading
    import time
    from watcherobot.runtime.manager import describe_command

    marker = tmp_path / "cancel"
    monkeypatch.setenv("WATCHER_RUNTIME_CANCEL_FILE", str(marker))
    timer = threading.Timer(0.2, marker.touch)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeActivationCancelled):
            describe_command([sys.executable, "-c", "import time; time.sleep(30)"])
        assert time.monotonic() - started < 5
    finally:
        timer.cancel()
        timer.join()


def test_cancelled_publication_does_not_publish_or_leave_staging(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime").write_bytes(b"runtime")
    marker = tmp_path / "cancel"
    monkeypatch.setenv("WATCHER_RUNTIME_CANCEL_FILE", str(marker))
    original = repository._copy_bundle

    def cancel_after_copy(src, dst):
        original(src, dst)
        marker.touch()

    monkeypatch.setattr(repository, "_copy_bundle", cancel_after_copy)
    root = tmp_path / "bundles"
    with pytest.raises(RuntimeActivationCancelled):
        repository.prepare_bundle(source, root)
    assert not list(root.glob(".staging-*"))
    assert not [p for p in root.iterdir() if len(p.name) == 64]


def test_cancelled_lock_wait_returns_before_lock_timeout(tmp_path, monkeypatch):
    marker = tmp_path / "cancel"
    monkeypatch.setenv("WATCHER_RUNTIME_CANCEL_FILE", str(marker))
    with repository.operation_lock(tmp_path):
        marker.touch()
        with pytest.raises(RuntimeActivationCancelled):
            with repository.operation_lock(tmp_path, timeout=0):
                pytest.fail("must not acquire cancelled operation")
