import pytest
import json

from watcherobot.runtime.registration import consume_launch, register_launch


def test_registration_is_exact_and_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    payload = {
        "application_dir": "project",
        "launcher": {"kind": "python", "executable": "python"},
    }
    request = register_launch(payload)
    assert consume_launch(request["local_registration"], payload) == {
        **payload,
        "environment": {},
    }
    with pytest.raises(FileNotFoundError):
        consume_launch(request["local_registration"], payload)


def test_registration_rejects_substitution_and_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    request = register_launch({"application_dir": "a"})
    with pytest.raises(ValueError):
        consume_launch(request["local_registration"], {"application_dir": "b"})
    with pytest.raises(ValueError):
        consume_launch("../other", {})


def test_registration_rejects_malformed_document(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    request = register_launch({})
    (tmp_path / "launch-grants" / (request["local_registration"] + ".json")).write_text(
        "[]"
    )
    with pytest.raises(ValueError, match="document"):
        consume_launch(request["local_registration"], {})


def test_registration_transfers_only_allowed_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    payload = {"application_dir": "project"}
    request = register_launch(payload)
    path = tmp_path / "launch-grants" / (request["local_registration"] + ".json")
    path.write_text(
        json.dumps({**payload, "environment": {"WATCHER_SERVER_DATA_DIR": "data"}})
    )
    assert consume_launch(request["local_registration"], payload)["environment"] == {
        "WATCHER_SERVER_DATA_DIR": "data"
    }
