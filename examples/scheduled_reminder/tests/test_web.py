"""``reminder.web`` 的集成测试：真实 HTTP 服务器 + urllib 客户端。"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from reminder.alarms import AlarmStore
from reminder.web import AlarmWebServer


@pytest.fixture()
def server(tmp_path: Path):
    store = AlarmStore(tmp_path / "alarms.json").load()
    web = AlarmWebServer(store, host="127.0.0.1", port=0)
    web.start()
    try:
        yield web
    finally:
        web.stop()


def _request(method: str, url: str, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, _parse_body(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_body(exc.read())


def _parse_body(payload: bytes):
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _url(server: AlarmWebServer, path: str = "/") -> str:
    return f"http://127.0.0.1:{server.port}{path}"


class TestPage:
    def test_index_page_served(self, server: AlarmWebServer) -> None:
        status, _ = _request("GET", _url(server, "/"))
        assert status == 200

    def test_index_page_contains_alarm_ui(self, server: AlarmWebServer) -> None:
        request = urllib.request.Request(_url(server, "/"))
        with urllib.request.urlopen(request, timeout=5) as response:
            content = response.read().decode("utf-8")
        assert "机器人闹钟" in content
        assert 'id="f-time"' in content


class TestAlarmApi:
    def test_empty_list(self, server: AlarmWebServer) -> None:
        status, data = _request("GET", _url(server, "/api/alarms"))
        assert status == 200
        assert data == {"alarms": []}

    def test_add_and_list(self, server: AlarmWebServer) -> None:
        status, data = _request("POST", _url(server, "/api/alarms"), {"time": "07:30", "text": "起床啦", "days": [0, 1, 2, 3, 4]})
        assert status == 201
        alarm_id = data["alarm"]["id"]
        assert data["alarm"]["time"] == "07:30"
        assert data["alarm"]["days"] == [0, 1, 2, 3, 4]

        status, data = _request("GET", _url(server, "/api/alarms"))
        assert status == 200
        assert [item["id"] for item in data["alarms"]] == [alarm_id]

    def test_add_validation_error(self, server: AlarmWebServer) -> None:
        status, data = _request("POST", _url(server, "/api/alarms"), {"time": "25:00", "text": "x"})
        assert status == 400
        assert "时间" in data["error"]

    def test_add_missing_fields(self, server: AlarmWebServer) -> None:
        status, data = _request("POST", _url(server, "/api/alarms"), {"time": "08:00"})
        assert status == 400
        assert "text" in data["error"]

    def test_toggle_enabled(self, server: AlarmWebServer) -> None:
        _, created = _request("POST", _url(server, "/api/alarms"), {"time": "08:00", "text": "x"})
        alarm_id = created["alarm"]["id"]

        status, data = _request("PATCH", _url(server, f"/api/alarms/{alarm_id}"), {"enabled": False})
        assert status == 200
        assert data["alarm"]["enabled"] is False

        status, data = _request("GET", _url(server, "/api/alarms"))
        assert data["alarms"][0]["enabled"] is False

    def test_delete(self, server: AlarmWebServer) -> None:
        _, created = _request("POST", _url(server, "/api/alarms"), {"time": "08:00", "text": "x"})
        alarm_id = created["alarm"]["id"]

        status, _ = _request("DELETE", _url(server, f"/api/alarms/{alarm_id}"))
        assert status == 204

        status, _ = _request("DELETE", _url(server, f"/api/alarms/{alarm_id}"))
        assert status == 404

        status, data = _request("GET", _url(server, "/api/alarms"))
        assert data == {"alarms": []}

    def test_unknown_path(self, server: AlarmWebServer) -> None:
        status, _ = _request("GET", _url(server, "/nope"))
        assert status == 404