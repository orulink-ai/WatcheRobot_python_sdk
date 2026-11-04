import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.state import MeetingMemory
from studio.scenes import Scene, EXPRESSIONS
from studio.agent import parse_reply
from studio.dialogue import Dialogue
from studio.service import MeetingService
from studio.config import Settings
from studio.robot import SDKRobot
import threading

@pytest.mark.parametrize('failures', [1, 2])
def test_recording_close_retries_timeout_once_and_retains_unconfirmed_session(failures):
    class Microphone:
        id = 42
        dropped_frames = decode_failures = 0
        attempts = 0
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.attempts += 1
            if self.attempts <= failures:
                raise TimeoutError('close ACK missing')
    microphone = Microphone()
    sdk = SimpleNamespace(microphone=SimpleNamespace(open_pcm=lambda **kw: microphone))
    stopped = threading.Event()
    stopped.set()
    robot = SDKRobot(sdk, stopped)
    if failures == 1:
        assert robot._record_utterance(Settings(), threading.Event()) == b''
        assert robot.microphone_session is None
        assert robot.microphone_stats['state'] == 'closed'
    else:
        with pytest.raises(TimeoutError, match='close ACK missing'):
            robot._record_utterance(Settings(), threading.Event())
        assert robot.microphone_session is microphone
        assert robot.microphone_stats['state'] == 'error'
    assert microphone.attempts == 2

def test_photo_uses_authored_expression_and_releases_after_capture(tmp_path):
    events = []
    class Image:
        def save(self, path): events.append("save")
    class Camera:
        def capture_with_feedback(self, **kwargs):
            events.append("capture")
            return Image()
    sdk = SimpleNamespace(camera=Camera(), supports=lambda capability: True, expression_runtime=SimpleNamespace(
        start=lambda preset: events.append(preset), stop=lambda: events.append("expression.stop")))
    robot = SDKRobot(sdk, threading.Event())
    asyncio.run(robot.photo(tmp_path / "photo.jpg"))
    assert events == ["capture", "save", "expression.stop"]

def test_photo_preview_requires_token_and_handles_missing_file(tmp_path):
    import re
    from fastapi.testclient import TestClient
    from studio.web import create_web_app
    from studio.config import ConfigStore
    store = ConfigStore(tmp_path / "settings.json")
    service = MeetingService(Robot(), Cloud(), store.settings, tmp_path)
    web_root = Path(__file__).resolve().parents[1] / "web"
    with TestClient(create_web_app(service, store, web_root)) as client:
        assert client.get("/api/photo").status_code == 403
        token = re.search(r'name="meeting-token" content="([^"]+)', client.get("/").text).group(1)
        client.headers["X-Meeting-Token"] = token
        assert client.get("/api/photo").status_code == 404
        service.last_photo = "missing.jpg"
        assert client.get("/api/photo").status_code == 404
        (tmp_path / "photos").mkdir(exist_ok=True)
        (tmp_path / "photos" / "missing.jpg").write_bytes(b"jpeg-test")
        response = client.get("/api/photo")
        assert response.content == b"jpeg-test"
        assert response.headers["content-type"] == "image/jpeg"

def test_manual_photo_is_single_capture_and_does_not_mark_first_meeting_complete(tmp_path):
    import re
    import time
    from fastapi.testclient import TestClient
    from studio.web import create_web_app
    from studio.config import ConfigStore
    class PhotoRobot(Robot):
        calls = 0
        async def photo(self, path):
            self.calls += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.sleep(0.1)
            path.write_bytes(b"camera-jpeg")
    robot = PhotoRobot()
    store = ConfigStore(tmp_path / "settings.json")
    service = MeetingService(robot, Cloud(), store.settings, tmp_path)
    service.device_id = "test-device"
    web_root = Path(__file__).resolve().parents[1] / "web"
    with TestClient(create_web_app(service, store, web_root)) as client:
        assert client.post("/api/photo").status_code == 403
        token = re.search(r'name="meeting-token" content="([^"]+)', client.get("/").text).group(1)
        client.headers["X-Meeting-Token"] = token
        assert client.post("/api/photo").status_code == 200
        assert client.post("/api/photo").status_code == 409
        deadline = time.monotonic() + 2
        while not service.last_photo and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.get("/api/photo").content == b"camera-jpeg"
    assert robot.calls == 1
    assert service.memory.get("test-device") is None

def test_expression_requires_latest_firmware_capability():
    sdk = SimpleNamespace(supports=lambda capability: False)
    robot = SDKRobot(sdk, threading.Event())
    with pytest.raises(RuntimeError, match="ESP32"):
        asyncio.run(robot.expression("curious"))


def test_missing_chat_audio_capability_is_actionable_in_service_log(tmp_path):
    calls = []
    sdk = SimpleNamespace(
        supports=lambda capability: capability != "expression.chat.audio.v1",
        expression_runtime=SimpleNamespace(start=lambda *a, **kw: calls.append(a)))
    robot = SDKRobot(sdk, threading.Event())
    service = MeetingService(robot, Cloud(), Settings(), tmp_path)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(robot.expression("sleepy"))
    detail = service.error_detail(caught.value)
    assert "expression.chat.audio.v1" in detail
    assert "ESP32" in detail
    assert not calls

def test_web_editor_is_authenticated_and_validates_scenes(tmp_path):
    import re
    from fastapi.testclient import TestClient
    from studio.web import create_web_app
    from studio.config import ConfigStore
    store = ConfigStore(tmp_path / "settings.json")
    service = MeetingService(Robot(), Cloud(), store.settings, tmp_path)
    web_root = Path(__file__).resolve().parents[1] / "web"
    with TestClient(create_web_app(service, store, web_root)) as client:
        assert client.get("/api/config").status_code == 403
        token = re.search(r'name="meeting-token" content="([^"]+)', client.get("/").text).group(1)
        client.headers["X-Meeting-Token"] = token
        assert len(client.get("/api/config").json()["scenes"]) == 6
        assert client.post("/api/config", json={"scenes": [{"pan": 180}]}).status_code == 422
        assert client.post("/api/config", json={"scenes": [{"text": "你好呀", "pan": 95}]}).status_code == 200
        assert store.settings.scenes[0].pan == 95

class Robot:
    async def expression(self, name): pass
    async def speak(self, pcm): pass
    async def move(self, *args): pass
    async def stop(self): pass
    async def photo(self, path): raise RuntimeError("camera failure")

class Cloud:
    async def tts(self, text): return b"pcm"
    async def chat(self, messages): return "你好呀"

def test_failed_photo_does_not_complete(tmp_path):
    service = MeetingService(Robot(), Cloud(), Settings(), tmp_path)
    service.device_id = service.active_device_id = "device-a"
    async def run():
        await service.turn("我叫小明")
        await service.turn("可以")
    asyncio.run(run())
    assert service.dialogue.stage == "consent"
    assert service.memory.get("device-a") is None

def test_declining_photo_completes_and_next_start_skips_boot(tmp_path):
    service = MeetingService(Robot(), Cloud(), Settings(voice_enabled=False), tmp_path)
    service.device_id = service.active_device_id = "device-a"
    async def run():
        await service.turn("我叫小明")
        await service.turn("不拍照")
        service.start()
        assert service.dialogue.stage == "chat"
        await service.stop()
    asyncio.run(run())
    assert service.memory.get("device-a")["name"] == "小明"

def test_rehearsal_service_does_not_persist(tmp_path):
    service = MeetingService(Robot(), Cloud(), Settings(), tmp_path)
    service.device_id = service.active_device_id = "device-a"
    service.rehearsal = True
    async def run():
        await service.turn("我叫小明")
        await service.turn("不拍照")
    asyncio.run(run())
    assert service.memory.get("device-a") is None

def test_completion_is_per_device_and_survives_restart(tmp_path):
    memory = MeetingMemory(tmp_path / "people.json")
    memory.complete("device-a", "小明", "", False)
    reloaded = MeetingMemory(tmp_path / "people.json")
    assert reloaded.get("device-a")["name"] == "小明"
    assert reloaded.get("device-b") is None

def test_rehearsal_never_marks_completion(tmp_path):
    memory = MeetingMemory(tmp_path / "people.json")
    memory.complete("device-a", "小明", "", True)
    assert memory.get("device-a") is None

def test_empty_identity_cannot_complete(tmp_path):
    with pytest.raises(ValueError):
        MeetingMemory(tmp_path / "people.json").complete("", "小明", "", False)

@pytest.mark.parametrize("answer", ["不可以", "如果可以的话", "可以吗", "你说可以是什么意思"])
def test_agent_text_cannot_authorize_photo(answer):
    dialogue = Dialogue()
    dialogue.next("我叫小明")
    assert not dialogue.next(answer).photo

@pytest.mark.parametrize('answer', ['可以拍照', '可以拍照。', '可以拍', '可以拍一张'])
def test_explicit_photo_permission_is_natural_and_one_shot(answer):
    dialogue = Dialogue()
    dialogue.next('我叫小明')
    assert dialogue.next(answer).photo
    assert not dialogue.next(answer).photo


@pytest.mark.parametrize('answer', ['可以拍照吗？', '不可以拍照', '如果可以拍照', '他说可以拍照'])
def test_photo_permission_does_not_match_substrings(answer):
    dialogue = Dialogue()
    dialogue.next('我叫小明')
    assert not dialogue.next(answer).photo


def test_scene_bounds_and_expression_contract():
    with pytest.raises(ValueError):
        Scene(pan=180)
    with pytest.raises(ValueError):
        Scene(expression="old_gif")
    assert EXPRESSIONS["curious"] == {"preset": "neutral"}
    assert EXPRESSIONS["warm"] == {"preset": "smile"}
    assert EXPRESSIONS["thinking"] == {"preset": "processing"}
    assert EXPRESSIONS["photo"] == {"preset": "photo"}
    assert EXPRESSIONS["wink"] == {"preset": "sunglasses"}

def test_normal_speech_uses_calm_audio_driven_expression():
    assert EXPRESSIONS["speaking"] == {"preset": "neutral"}
    assert EXPRESSIONS["listening"] == {"preset": "neutral"}
    for emotion in ("neutral", "smile", "happy", "anger", "concentration", "sad", "speechless"):
        assert EXPRESSIONS[emotion] == {"preset": emotion}


def test_speech_failure_restores_neutral(tmp_path):
    events = []
    class FailingRobot(Robot):
        async def expression(self, name): events.append(name)
        async def speak(self, pcm): raise RuntimeError("playback failure")
    service = MeetingService(FailingRobot(), Cloud(), Settings(), tmp_path)
    with pytest.raises(RuntimeError, match="playback failure"):
        asyncio.run(service.say("你好"))
    assert events == ["speaking", "neutral"]


def test_only_completed_agent_messages_are_used():
    events = [{"type":"item.completed", "item":{"type":"command_execution","text":"wrong"}},
              {"type":"item.completed", "item":{"type":"agent_message","text":"你好呀"}},
              {"type":"turn.completed"}]
    assert parse_reply(events) == "你好呀"
    with pytest.raises(RuntimeError):
        parse_reply(events + [{"type":"turn.failed"}])


def test_stop_reports_failed_component_and_still_stops_other_components():
    calls = []
    def microphone_close():
        calls.append('microphone')
        raise RuntimeError('session no longer exists')
    def motion_stop(): calls.append('motion')
    def audio_stop(): calls.append('audio')
    def expression_stop(): calls.append('expression')
    sdk = SimpleNamespace(motion=SimpleNamespace(stop=motion_stop),
                          audio=SimpleNamespace(stop=audio_stop),
                          expression_runtime=SimpleNamespace(stop=expression_stop))
    robot = SDKRobot(sdk, threading.Event())
    robot.microphone_session = SimpleNamespace(close=microphone_close)
    with pytest.raises(RuntimeError, match='microphone.close:RuntimeError: session no longer exists'):
        asyncio.run(robot.stop())
    assert calls == ['microphone', 'motion', 'audio', 'expression']
    assert robot.microphone_session is not None


def test_cleanup_log_preserves_component_failure_and_redacts_secrets(tmp_path):
    class FailedRobot(Robot):
        async def stop(self):
            raise RuntimeError('设备停止请求未全部确认：microphone.close:RuntimeError: private-token')
    settings = Settings()
    settings.stt_token = 'private-token'
    service = MeetingService(FailedRobot(), Cloud(), settings, tmp_path)
    asyncio.run(service.stop())
    assert service.cleanup_required
    assert 'microphone.close' in service.events[-1]['text']
    assert 'private-token' not in service.events[-1]['text']

def test_photo_old_firmware_preserves_current_expression(tmp_path):
    events = []
    sdk = SimpleNamespace(supports=lambda c: c != 'camera.capture.seamless_feedback.v1',
                          expression_runtime=SimpleNamespace(stop=lambda: events.append('stop')))
    robot = SDKRobot(sdk, threading.Event())
    from watcherobot.errors import WatcheRobotError
    with pytest.raises(WatcheRobotError, match='无缝'):
        asyncio.run(robot.photo(tmp_path / 'photo.jpg'))
    assert events == []


def test_photo_prepare_failure_preserves_current_expression(tmp_path):
    events = []
    def fail(**kwargs):
        raise TimeoutError('camera preparation failed')
    sdk = SimpleNamespace(supports=lambda c: True, camera=SimpleNamespace(capture_with_feedback=fail),
                          expression_runtime=SimpleNamespace(stop=lambda: events.append('stop')))
    robot = SDKRobot(sdk, threading.Event())
    with pytest.raises(TimeoutError):
        asyncio.run(robot.photo(tmp_path / 'photo.jpg'))
    assert events == []
