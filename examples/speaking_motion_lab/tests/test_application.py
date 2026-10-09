"""Verify the real SDK manages this Application; no second Daemon implementation."""
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

import httpx

from watcherobot.distribution.source_files import collect_application_source_files
from watcherobot.runtime.daemon.runtime import DaemonRuntime


APP_ROOT = Path(__file__).resolve().parents[1]


def test_publish_snapshot_contains_built_assets_and_excludes_development_files():
    files = {p.as_posix() for p in collect_application_source_files(APP_ROOT)}
    assert "app.py" in files and "behavior_driver/driver.py" in files
    assert "web/joyinside-preview.html" in files and "web/web-build.json" in files
    assert any(p.endswith(".glb") for p in files)
    assert not any(p.startswith(("tests/", ".web-build-")) or "__pycache__" in p for p in files)


def test_managed_snapshot_starts_plans_ignores_unknown_frames_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_BEHAVIOR_LAB_NO_BROWSER", "1")
    snapshot = tmp_path / "application"
    snapshot.mkdir()
    # Copy only publisher-selected files. The source checkout is not needed at runtime.
    for source in collect_application_source_files(APP_ROOT):
        target = snapshot / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(APP_ROOT / source, target)

    async def scenario():
        runtime = DaemonRuntime(application_dir=tmp_path / "unselected", current_app=None,
            external_host="127.0.0.1", external_port=0, control_port=0,
            pairing_udp_port=0, preview_udp_port=0, managed_app_root=Path(sys.executable).parent.parent,
            application_log_dir=tmp_path / "logs", daemon_log_path=tmp_path / "daemon.jsonl")
        await runtime.start()
        try:
            runtime.select_application(str(snapshot), "python", sys.executable)
            await runtime.start_application()
            log_path = runtime.application_logs.log_path("com.orulink.speaking_motion_lab")
            url = None
            for _ in range(150):
                if log_path.exists():
                    match = re.search(r"http://127\.0\.0\.1:\d+/joyinside-preview\.html", log_path.read_text(encoding="utf-8"))
                    if match:
                        url = match.group(0)
                        break
                await asyncio.sleep(.05)
            assert url, "Application did not announce its UI; inspect captured process logs"
            async with httpx.AsyncClient(trust_env=False) as client:
                html = (await client.get(url)).text
                config = json.loads(re.search(r'<script id="behavior-lab-config" type="application/json">(.*?)</script>', html).group(1))
                base = url.rsplit("/", 1)[0]
                status = (await client.get(base + "/api/status")).json()
                assert status["deviceExecution"] is False
                payload = {"schema": "watche.behavior.request.v1", "audio": {"durationMs": 4000,
                    "stepMs": 20, "levels": [250] * 200}, "transcript": "是的。不可以。"}
                result = await client.post(base + "/api/behavior/plan", json=payload,
                    headers={"X-Behavior-Run": config["runCredential"]})
                assert result.status_code == 200
                assert [cue["intent"] for cue in result.json()["cues"]] == ["affirm", "deny"]
                # Unsupported Desktop business protocols are ignored by this preview App.
                from watcherobot.runtime.daemon.application.session import ApplicationChannel
                await runtime.application.bridge.send_to_application(ApplicationChannel.DESKTOP,
                    '{"type":"ctrl.microphone.open"}')
                assert (await client.get(base + "/api/status")).status_code == 200
                await runtime.stop_application()
                assert runtime.application.process_id is None
                try:
                    await client.get(base + "/api/status")
                except httpx.ConnectError:
                    pass
                else:
                    raise AssertionError("Application stop left its HTTP listener alive")
        finally:
            await runtime.stop()
    asyncio.run(scenario())
