from __future__ import annotations

import json
from importlib import resources
from urllib.request import urlopen

import pytest

from watcherobot.diagnostics.rtc.server import RtcDiagnosticsServer


def test_diagnostics_server_binds_loopback_and_serves_runtime_config() -> None:
    server = RtcDiagnosticsServer(
        control_url="http://127.0.0.1:8767",
        external_url="ws://127.0.0.1:8765",
        port=0,
    )
    try:
        url = server.start()
        assert url.startswith("http://127.0.0.1:")
        with urlopen(f"{url}/config.json", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store"
        assert payload == {
            "controlUrl": "http://127.0.0.1:8767",
            "externalUrl": "ws://127.0.0.1:8765",
            "protocol": "watcher-rtc/1",
            "pythonSdkVersion": pytest.importorskip("watcherobot").__version__,
            "pythonSdkCommit": "",
        }
        with urlopen(url, timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "WatcheRobot RTC Diagnostics" in html
        assert 'type="module"' in html
    finally:
        server.stop()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.9", "localhost"])
def test_diagnostics_server_rejects_non_literal_loopback_host(host: str) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        RtcDiagnosticsServer(
            control_url="http://127.0.0.1:8767",
            external_url="ws://127.0.0.1:8765",
            host=host,
        )


def test_browser_rtc_contract_keeps_media_latest_only_and_stress_separate() -> None:
    script = (
        resources.files("watcherobot.diagnostics.rtc.static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    assert "createDataChannel('mjpeg-data',{ordered:false,maxPacketLifeTime:200})" in script
    assert "createDataChannel('rtc-stress',{ordered:false,maxPacketLifeTime:100})" in script
    assert "pendingFrame={sequence:" in script
    assert "if(!decodeBusy)decodeLatest()" in script
    assert "new Uint8Array(16000)" in script
    assert "},64)" in script
    assert "jitterBufferDelay" in script
