"""Application web boundary and portable static resource delivery."""
import json

import pytest
from fastapi.testclient import TestClient

from web_server import create_web_app


@pytest.fixture
def client(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "joyinside-preview.html").write_text("<html><head></head><body>lab</body></html>", encoding="utf-8")
    (web / "assets").mkdir()
    (web / "assets" / "face.wasm").write_bytes(b"wasm")
    with TestClient(create_web_app(web, run_credential="test-run"), base_url="http://127.0.0.1") as instance:
        yield instance


def payload():
    return {"schema": "watche.behavior.request.v1", "audio": {"durationMs": 1000, "levels": [200] * 50}, "transcript": "是的。"}


def test_web_bootstrap_and_local_assets(client):
    html = client.get("/joyinside-preview.html").text
    assert 'id="behavior-lab-config"' in html
    assert '"apiBase": "/api/behavior"' in html
    assert '"runCredential": "test-run"' in html
    assert client.get("/assets/face.wasm").content == b"wasm"
    assert client.get("/api/status").json()["deviceExecution"] is False
    assert client.get("/app.py").status_code == 404
    assert client.get("/", follow_redirects=False).headers["location"] == "/joyinside-preview.html"


def test_planning_api_requires_run_credential_and_same_origin(client):
    assert client.post("/api/behavior/plan", json=payload()).status_code == 403
    headers = {"X-Behavior-Run": "test-run", "Origin": "https://unrelated.example"}
    assert client.post("/api/behavior/plan", json=payload(), headers=headers).status_code == 403
    headers["Origin"] = "http://127.0.0.1"
    response = client.post("/api/behavior/plan", json=payload(), headers=headers)
    assert response.status_code == 200
    plan = response.json()
    assert plan["schema"] == "watche.behavior.plan.v1"
    assert plan["cues"][0]["intent"] == "affirm"
    assert plan["frames"][-1]["mouthLevel"] == 0
    assert "test-run" not in json.dumps(plan)
    assert client.get("/api/status", headers={"Host": "unrelated.example"}).status_code == 400


def test_invalid_inputs_have_structured_errors(client):
    body = payload()
    body["audio"]["levels"] = [200]
    response = client.post("/api/behavior/plan", json=body, headers={"X-Behavior-Run": "test-run"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_behavior_request"
