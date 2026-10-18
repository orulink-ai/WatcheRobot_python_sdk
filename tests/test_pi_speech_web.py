import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading
import logging

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location("pi_speech_service", ROOT / "service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Session:
    def __init__(self, text):
        self.text = text
        self.closed = False
        self.cancelled = threading.Event()
        self.played = []

    def recognize(self, timeout=30):
        if self.text is None:
            self.cancelled.wait(3)
            raise RuntimeError("cancelled")
        return self.text

    def say(self, text, timeout=60):
        self.played.append(text)

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed = True


class Robot:
    device_info = {"device_id": "test-robot"}
    def __init__(self):
        self.sessions = []
        self.speech = SimpleNamespace(open=self.open, close=lambda: None)

    def refresh_device_info(self, timeout=1):
        return self.device_info

    def open(self):
        session = Session(["first", "second"][len(self.sessions)] if len(self.sessions) < 2 else None)
        self.sessions.append(session)
        return session


class Agent:
    def __init__(self):
        self.prompts = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def ask(self, text, *, on_speak):
        self.prompts.append(text)
        return "reply " + str(len(self.prompts))

    async def close(self):
        self.closed = True


class AsyncBackend:
    def __init__(self, configured=True):
        self.configured = configured
        self.session = None
        self.opens = 0
    def snapshot(self):
        return {"id": "doubao", "label": "Application 豆包", "configured": self.configured}
    async def open(self):
        self.opens += 1
        played = []
        async def recognize(timeout=30): return "搜索小狮日记"
        async def say(text): played.append(text)
        async def cancel(): pass
        async def close(): self.session.closed = True
        self.session = SimpleNamespace(recognize=recognize, say=say, cancel=cancel,
                                       close=close, played=played, closed=False)
        return self.session
    async def close(self):
        if self.session: await self.session.close()


def test_async_doubao_backend_runs_through_pi_without_device_speech(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setenv("WATCHER_SPEECH_ONCE", "1")
    async def check():
        robot, agent, backend = Robot(), Agent(), AsyncBackend()
        service = module.ConversationService(
            SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test")),
            agent_factory=lambda: agent, speech_backends={"doubao": backend}, backend_name="doubao")
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_until(lambda: service.turns == 1 and backend.session.closed)
            assert robot.sessions == [], "Doubao must not open the firmware speech session"
            assert backend.opens == 1
            assert "搜索小狮日记" in agent.prompts[0]
            assert backend.session.played == ["reply 1"]
            assert service.snapshot()["speech_backend"] == "doubao"
        finally:
            service.shutdown()
            await runner
    asyncio.run(check())


def test_backend_selection_api_validates_and_does_not_expose_credentials(monkeypatch):
    module = load(monkeypatch)
    backend, saved = AsyncBackend(), []
    service = module.ConversationService(
        SimpleNamespace(robot=Robot()), speech_backends={"doubao": backend},
        backend_name="doubao", save_backend=saved.append)
    with TestClient(module.create_web_app(service, ROOT / "web")) as client:
        assert client.post("/api/speech/backend", json={"backend": "unknown"}).status_code == 400
        assert client.post("/api/speech/backend", json={"backend": "doubao"}, headers={"Origin": "https://outside.example"}).status_code == 403
        response = client.post("/api/speech/backend", json={"backend": "doubao"})
        assert response.status_code == 200
        assert response.json()["speech_backend"] == "doubao"
        assert response.json()["speech_backends"][0]["configured"] is True
        assert "access_token" not in response.text
        assert saved == ["doubao"]
        backend.configured = False
        assert client.post("/api/speech/backend", json={"backend": "doubao"}).status_code == 400


@pytest.mark.parametrize("busy", ["enabled", "opening", "session", "cleanup_pending"])
def test_switch_rejected_while_audio_owned(monkeypatch, busy):
    module = load(monkeypatch)
    backend, saved = AsyncBackend(), []
    service = module.ConversationService(SimpleNamespace(robot=Robot()),
        speech_backends={"doubao": backend}, backend_name="doubao", save_backend=saved.append)
    setattr(service, busy, True)
    with TestClient(module.create_web_app(service, ROOT / "web")) as client:
        assert client.post("/api/speech/backend", json={"backend": "doubao"}).status_code == 409
        assert saved == []


def test_failed_audio_cleanup_retains_owner_until_confirmed(monkeypatch):
    module = load(monkeypatch)
    async def check():
        service = module.ConversationService(SimpleNamespace(robot=Robot()),
            speech_backends={"doubao": AsyncBackend()}, backend_name="doubao")
        calls = 0
        async def close():
            nonlocal calls
            calls += 1
            if calls == 1: raise TimeoutError("unconfirmed close")
        session = SimpleNamespace(close=close)
        service.session = session
        with pytest.raises(TimeoutError): await service._close_session()
        assert service.session is session and service.cleanup_pending
        with pytest.raises(module.ConnectionError): service.select_backend("doubao")
        await service._close_session()
        service.select_backend("doubao")
        assert service.session is None and not service.cleanup_pending
    asyncio.run(check())


def test_preference_save_failure_does_not_change_backend(monkeypatch):
    module = load(monkeypatch)
    def fail(_name): raise OSError("private filesystem error")
    service = module.ConversationService(SimpleNamespace(robot=Robot()),
        speech_backends={"joyinside": AsyncBackend(), "doubao": AsyncBackend()}, save_backend=fail)
    with TestClient(module.create_web_app(service, ROOT / "web")) as client:
        response = client.post("/api/speech/backend", json={"backend": "doubao"})
        assert response.status_code == 503
        assert "private filesystem" not in response.text
        assert service.backend_name == "joyinside"


def test_desktop_start_during_unconfirmed_cleanup_keeps_controls_alive(monkeypatch):
    module = load(monkeypatch)
    async def check():
        received = 0
        async def receive(timeout):
            nonlocal received
            received += 1
            if received == 1: return '{"type":"ctrl.microphone.open"}'
            await asyncio.sleep(.02)
            raise TimeoutError
        context = SimpleNamespace(robot=Robot(), shutdown_requested=False,
                                  desktop=SimpleNamespace(receive=receive))
        service = module.ConversationService(context)
        service.cleanup_pending = True
        task = asyncio.create_task(service.desktop_controls())
        try:
            await asyncio.sleep(.1)
            assert not task.done(), "a rejected start must not terminate desktop controls"
            assert not service.enabled and "尚未释放" in service.error
        finally:
            service.stopping = True
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


async def wait_until(check):
    for _ in range(150):
        if check(): return
        await asyncio.sleep(.02)
    raise AssertionError("condition not reached")


@pytest.mark.parametrize("text", ["搜索小师日记的视频", "狮子正在做手工，不是在找账号"])
def test_voice_prompt_supplies_canonical_names_and_keeps_original_input(monkeypatch, text):
    module = load(monkeypatch)
    async def check():
        agent = Agent()
        context = SimpleNamespace(robot=Robot(), shutdown_requested=False)
        service = module.ConversationService(context, agent_factory=lambda: agent)
        service.start()
        assert await service._ask(service.generation, text) == "reply 1"
        prompt = agent.prompts[0]
        for name in ("小狮日记", "手工耿", "贪玩歌姬小宁子"):
            assert name in prompt
        assert "狮子的“狮”" in prompt
        assert "不要把普通词语强行替换成专名" in prompt
        assert prompt.endswith("用户语音识别原文：\n" + text)
        await agent.close()
    asyncio.run(check())


def test_one_start_runs_multiple_turns_and_pause_drains_microphone(monkeypatch):
    module = load(monkeypatch)
    async def check():
        robot, agent = Robot(), Agent()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=lambda: agent)
        runner = asyncio.create_task(service.run())
        await asyncio.sleep(.1)
        assert robot.sessions == [], "standby must not record"
        service.start()
        await wait_until(lambda: len(robot.sessions) == 3)
        assert len(agent.prompts) == 2
        assert robot.sessions[0].played == ["reply 1"]
        assert robot.sessions[1].played == ["reply 2"]
        assert all(s.closed for s in robot.sessions[:2])
        service.pause()
        # Audio close precedes behavior cleanup; wait for the full pause before
        # asserting its terminal state, rather than racing the service owner.
        await wait_until(lambda: robot.sessions[2].closed and service.snapshot()["phase"] == "paused")
        assert robot.sessions[2].cancelled.is_set()
        assert service.snapshot()["phase"] == "paused"
        assert len(service.snapshot()["messages"]) == 4
        service.shutdown()
        await runner
        assert agent.closed
    asyncio.run(check())


def test_local_web_exposes_real_state_and_rejects_other_origins(monkeypatch):
    module = load(monkeypatch)
    c = SimpleNamespace(robot=Robot(), shutdown_requested=False, logger=logging.getLogger("test"))
    service = module.ConversationService(c)
    with TestClient(module.create_web_app(service, ROOT / "web")) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").json()["enabled"] is False
        assert client.post("/api/start", headers={"Origin": "https://outside.example"}).status_code == 403
        assert client.post("/api/start").json()["enabled"] is True
        assert client.post("/api/pause").json()["enabled"] is False
        assert client.get("/api/state", headers={"Host": "outside.example"}).status_code == 403


@pytest.mark.parametrize("stage", ["open", "agent", "say"])
@pytest.mark.parametrize("empty_message", [False, True], ids=["message", "empty_message"])
def test_non_recognition_timeout_stops_and_remains_visible(monkeypatch, stage, empty_message):
    module = load(monkeypatch)

    async def check():
        robot, agent = Robot(), Agent()
        original_open = robot.speech.open
        open_calls = 0

        def fail():
            raise TimeoutError("" if empty_message else f"{stage} timed out")

        async def fail_agent(text, *, on_speak):
            fail()

        def open_session():
            nonlocal open_calls
            open_calls += 1
            if stage == "open":
                fail()
            session = original_open()
            if stage == "say":
                session.say = lambda **kwargs: fail()
            return session

        robot.speech.open = open_session
        if stage == "agent":
            agent.ask = fail_agent
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context, agent_factory=lambda: agent)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_until(lambda: bool(service.error))
            await asyncio.sleep(1.2)
            assert service.snapshot()["phase"] == "error"
            assert service.snapshot()["enabled"] is False
            expected_error = "TimeoutError" if empty_message else f"{stage} timed out"
            assert expected_error in service.snapshot()["error"]
            assert open_calls == 1, "a failed handshake, model request or playback must not retry silently"
        finally:
            service.shutdown()
            await runner

    asyncio.run(check())


@pytest.mark.parametrize("empty_message", [False, True], ids=["message", "empty_message"])
def test_cleanup_failure_keeps_primary_error_and_idle_error_state(monkeypatch, empty_message):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        fail_cleanup = True
        cleanup_attempted = threading.Event()

        def open_session():
            raise RuntimeError("speech handshake failed")

        def close_session():
            cleanup_attempted.set()
            if fail_cleanup:
                raise TimeoutError("" if empty_message else "speech cleanup timed out")

        robot.speech = SimpleNamespace(open=open_session, close=close_session)
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_until(cleanup_attempted.is_set)
            await asyncio.sleep(1.2)
            state = service.snapshot()
            assert "speech handshake failed" in state["error"]
            expected_error = "TimeoutError" if empty_message else "speech cleanup timed out"
            assert expected_error in state["error"]
            assert state["phase"] == "error"
            assert state["enabled"] is False
        finally:
            fail_cleanup = False
            service.shutdown()
            await runner

    asyncio.run(check())


def test_pause_during_open_waits_for_cleanup_before_reporting_paused(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        opening, release = threading.Event(), threading.Event()
        session = Session("must not be recognized")

        def open_session():
            opening.set()
            assert release.wait(3)
            return session

        robot.speech.open = open_session
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_until(opening.is_set)
            service.pause()
            assert service.snapshot()["phase"] == "stopping"
            assert service.snapshot()["enabled"] is False
            release.set()
            await wait_until(lambda: session.closed and service.phase == "paused")
            assert service.snapshot()["messages"] == []
        finally:
            release.set()
            service.shutdown()
            await runner

    asyncio.run(check())


def test_recognition_timeout_closes_session_before_listening_again(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()

        def silence(**kwargs):
            raise TimeoutError("recognition timed out")

        def open_session():
            session = Session(None)
            if not robot.sessions:
                session.recognize = silence
            else:
                assert robot.sessions[-1].closed
            robot.sessions.append(session)
            return session

        robot.speech.open = open_session
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_until(lambda: len(robot.sessions) == 2 and service.phase == "listening")
            assert service.error == ""
            assert service.enabled is True
            service.pause()
            await wait_until(lambda: robot.sessions[-1].closed and service.phase == "paused")
        finally:
            service.shutdown()
            await runner

    asyncio.run(check())
