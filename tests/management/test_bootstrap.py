from pathlib import Path

from watcherobot.management.bootstrap import prepare_request
from watcherobot.management.service import Manager

from .test_service import request


def test_forwarding_preserves_bundled_baseline_and_fallback_command(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    original = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    baseline = original.baseline()
    command = original.command(baseline)
    forwarded = prepare_request(original)
    monkeypatch.setattr("watcherobot.management.service.__version__", "99.0.0")
    replacement = Manager(forwarded, root=tmp_path / "manager", live=lambda: None)
    assert replacement.baseline() == baseline
    assert replacement.command(baseline) == command


def test_forwarding_does_not_replace_original_baseline_a_second_time(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    original = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    first = prepare_request(original)
    second = prepare_request(
        Manager(first, root=tmp_path / "manager", live=lambda: None)
    )
    assert first == second


def test_distribution_only_forwards_to_a_different_environment(tmp_path, monkeypatch):
    import sys

    from watcherobot.management.bootstrap import distribution_command

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setattr(Manager, "selection", lambda self: {"kind": "bundled"})
    assert distribution_command() is None
    monkeypatch.setattr(
        Manager, "selection", lambda self: {"kind": "environment", "root": sys.prefix}
    )
    assert distribution_command() is None
    monkeypatch.setattr(
        Manager,
        "selection",
        lambda self: {
            "kind": "environment",
            "root": str(tmp_path / "selected"),
            "contracts": {"manager": 1, "management": 1, "distribution": 1},
        },
    )
    monkeypatch.setattr(
        Manager,
        "command",
        lambda self, target, entry: [str(Path(target["root"]) / "bin" / "python")],
    )
    assert distribution_command()[0] == str(tmp_path / "selected" / "bin" / "python")


def test_repair_always_uses_the_bundled_rescue_entry(tmp_path, monkeypatch):
    from watcherobot.management.bootstrap import forward_management

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(
        {**request(), "action": "repair"}, root=tmp_path / "manager", live=lambda: None
    )
    monkeypatch.setattr(
        manager,
        "selection",
        lambda: (_ for _ in ()).throw(
            AssertionError("repair must not load a broken SDK")
        ),
    )
    assert forward_management(manager) is None
