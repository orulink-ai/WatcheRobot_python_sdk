"""Pairing stays on Daemon management; conversation keeps its device channel."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def load(monkeypatch, filename):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location("pi_pairing_" + filename, ROOT / (filename + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pair_uses_injected_management_endpoint_and_filters_credentials(monkeypatch):
    module = load(monkeypatch, "connection")
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps({"device": {
            "state": "discovering", "online": False, "device_id": "robot",
            "token": "secret", "preview_websocket_url": "ws://secret",
        }}).encode())

    manager = module.DaemonConnection("http://127.0.0.1:9876/daemon/devices", opener=opener)
    result = manager.pair("012345")
    assert calls[0].full_url == "http://127.0.0.1:9876/daemon/devices/pair"
    assert json.loads(calls[0].data) == {"pairing_code": "012345", "target_mode": "python_sdk"}
    assert result == {"state": "discovering", "online": False, "device_id": "robot", "last_error": None}
    assert manager.status() == result
    assert calls[1].get_method() == "GET"


@pytest.mark.parametrize("code", ["device_connect_timeout", "pairing_not_found"])
def test_actual_daemon_failure_stage_is_preserved(monkeypatch, code):
    module = load(monkeypatch, "connection")
    def opener(*args, **kwargs):
        return io.BytesIO(json.dumps({"device": {"online": False, "state": "idle", "last_error": code}}).encode())
    manager = module.DaemonConnection("http://localhost:1234/daemon/devices", opener=opener)
    assert manager.status()["last_error"] == code


@pytest.mark.parametrize("code", ["", "12345", "1234567", "１２３４５６", "abcdef", 123456, None])
def test_invalid_code_never_contacts_daemon(monkeypatch, code):
    module = load(monkeypatch, "connection")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid code must not leave the Application")
    manager = module.DaemonConnection("http://localhost:1234/daemon/devices", opener=forbidden)
    with pytest.raises(module.ConnectionError) as error:
        manager.pair(code)
    assert error.value.status_code == 400


@pytest.mark.parametrize("failure,status", [
    (HTTPError("http://localhost", 409, "secret", {}, io.BytesIO(b"secret")), 409),
    (HTTPError("http://localhost", 400, "secret", {}, io.BytesIO(b"secret")), 400),
    (URLError("secret"), 503),
])
def test_management_errors_do_not_echo_credentials(monkeypatch, failure, status):
    module = load(monkeypatch, "connection")
    def fail(*args, **kwargs):
        raise failure
    manager = module.DaemonConnection("http://localhost:1234/daemon/devices", opener=fail)
    with pytest.raises(module.ConnectionError) as error:
        manager.pair("012345")
    assert error.value.status_code == status
    assert "secret" not in str(error.value)
    assert "012345" not in str(error.value)


def test_pairing_routes_report_pending_then_connected_and_block_cross_origin(monkeypatch):
    module = load(monkeypatch, "service")
    context = SimpleNamespace(robot=SimpleNamespace(device_info={}))
    service = module.ConversationService(context)
    class Connection:
        online = False
        codes = []
        def status(self):
            return {"online": self.online, "state": "connected" if self.online else "discovering"}
        def pair(self, code):
            self.codes.append(code)
            return self.status()
    connection = Connection()
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        assert client.post("/api/pair", json={"pairing_code": "123456"}, headers={"Origin": "https://outside.example"}).status_code == 403
        response = client.post("/api/pair", json={"pairing_code": "123456"})
        assert response.status_code == 202
        assert response.json()["online"] is False
        assert connection.codes == ["123456"]
        connection.online = True
        assert client.get("/api/connection").json()["online"] is True
        service.enabled = True
        assert client.post("/api/pair", json={"pairing_code": "654321"}).status_code == 409
        assert connection.codes == ["123456"]


@pytest.mark.parametrize("body", [None, [], {"pairing_code": 123456}, {"pairing_code": "secret"}])
def test_web_invalid_pairing_is_safe(monkeypatch, body):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    connection = SimpleNamespace(pair=lambda code: pytest.fail("must validate before forwarding"))
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        response = client.post("/api/pair", json=body)
        assert response.status_code == 400
        assert "secret" not in response.text


def test_web_missing_manager_reports_unavailable(monkeypatch):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    with TestClient(module.create_web_app(service, ROOT / "web")) as client:
        assert client.get("/api/connection").status_code == 503


def test_start_requires_connected_robot_when_management_is_available(monkeypatch):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    connection = SimpleNamespace(status=lambda: {"online": False, "state": "idle"})
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        assert client.post("/api/start").status_code == 409
        assert service.enabled is False
        connection.status = lambda: {"online": True, "state": "connected"}
        assert client.post("/api/start").status_code == 200
        assert service.enabled is True


@pytest.mark.parametrize("url", ["http://remote.example/daemon/devices", "https://localhost/daemon/devices",
                                 "http://localhost/wrong", "http://localhost/daemon/devices?url=remote"])
def test_manager_rejects_non_injected_management_urls(monkeypatch, url):
    module = load(monkeypatch, "connection")
    with pytest.raises(ValueError):
        module.DaemonConnection(url)


@pytest.mark.parametrize("field", ["opening", "session"])
def test_pairing_waits_for_microphone_cleanup(monkeypatch, field):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    setattr(service, field, True)
    connection = SimpleNamespace(pair=lambda code: pytest.fail("speech still owns the robot"))
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        assert client.post("/api/pair", json={"pairing_code": "123456"}).status_code == 409


def test_pairing_rejects_duplicate_and_start_while_request_pending(monkeypatch):
    import threading
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    entered, release = threading.Event(), threading.Event()
    def pair(code):
        entered.set()
        assert release.wait(3)
        return {"online": False, "state": "discovering"}
    with TestClient(module.create_web_app(service, ROOT / "web", connection=SimpleNamespace(pair=pair))) as client:
        responses = []
        thread = threading.Thread(target=lambda: responses.append(client.post("/api/pair", json={"pairing_code": "123456"})))
        thread.start()
        try:
            assert entered.wait(3)
            assert client.post("/api/pair", json={"pairing_code": "654321"}).status_code == 409
            assert client.post("/api/start").status_code == 409
        finally:
            release.set()
            thread.join(3)
        assert responses[0].status_code == 202


def test_stale_cleanup_pairing_reports_restart_required_without_contacting_daemon(monkeypatch):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    service.cleanup_pending = True
    service.session = object()
    connection = SimpleNamespace(pair=lambda code: pytest.fail("unconfirmed cleanup must retain ownership"))
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        response = client.post("/api/pair", json={"pairing_code": "123456"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "speech_cleanup_required"
    assert "重启" in response.json()["error"]
    assert service.cleanup_pending is True
    assert service.session is not None


def test_active_conversation_pairing_reports_busy_without_contacting_daemon(monkeypatch):
    module = load(monkeypatch, "service")
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})))
    service.enabled = True
    service.cleanup_pending = True
    connection = SimpleNamespace(pair=lambda code: pytest.fail("active conversation must retain ownership"))
    with TestClient(module.create_web_app(service, ROOT / "web", connection=connection)) as client:
        response = client.post("/api/pair", json={"pairing_code": "123456"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "conversation_busy"
