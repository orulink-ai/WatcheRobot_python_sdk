import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading
import logging

from fastapi.testclient import TestClient

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
        if isinstance(self.text, list):
            text = self.text.pop(0) if self.text else None
        else:
            text = self.text
        if text is None:
            self.cancelled.wait(3)
            raise RuntimeError("cancelled")
        return text

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
        session = Session(["first", "second"] if not self.sessions else [])
        self.sessions.append(session)
        return session


class Agent:
    def __init__(self):
        self.prompts = []
        self.closed = False
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def ask(self, text, *, on_text=None):
        self.prompts.append(text)
        reply = "reply " + str(len(self.prompts))
        if on_text:
            await on_text(reply)
        return reply

    async def close(self):
        self.closed = True


async def wait_until(check):
    for _ in range(150):
        if check(): return
        await asyncio.sleep(.02)
    raise AssertionError("condition not reached")


def test_one_start_runs_multiple_turns_and_pause_drains_microphone(monkeypatch):
    module = load(monkeypatch)
    async def check():
        robot, agent = Robot(), Agent()
        original_open = robot.speech.open
        def open_after_pi_warmup():
            assert agent.entered, "start Pi before waiting for the first microphone input"
            return original_open()
        robot.speech.open = open_after_pi_warmup
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=lambda: agent)
        runner = asyncio.create_task(service.run())
        await asyncio.sleep(.1)
        assert robot.sessions == [], "standby must not record"
        service.start()
        await wait_until(lambda: service.turns == 2)
        assert len(agent.prompts) == 2
        assert len(robot.sessions) == 1, "normal turns must reuse the reserved audio session"
        assert robot.sessions[0].played == ["reply 1", "reply 2"]
        assert not robot.sessions[0].closed
        service.pause()
        await wait_until(lambda: robot.sessions[0].closed)
        assert robot.sessions[0].cancelled.is_set()
        assert service.snapshot()["phase"] == "paused"
        assert len(service.snapshot()["messages"]) == 4
        service.shutdown()
        await runner
        assert agent.closed
    asyncio.run(check())


def test_sentence_plays_while_pi_is_still_generating_and_pause_cancels_all(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        class StreamingAgent(Agent):
            async def ask(self, text, *, on_text):
                await on_text("第一句话。")
                await wait_until(lambda: robot.sessions[0].played == ["第一句话。"])
                await on_text("第二句仍在生成")
                await asyncio.Event().wait()

        agent = StreamingAgent()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=lambda: agent)
        service.start()
        runner = asyncio.create_task(service.run())
        try:
            await wait_until(lambda: robot.sessions and robot.sessions[0].played)
            assert service.turns == 0, "first sentence must play before Pi settles"
            assert service.snapshot()["messages"][-1]["streaming"] is True
            service.pause()
            await wait_until(lambda: robot.sessions[0].closed and agent.closed)
            assert robot.sessions[0].played == ["第一句话。"], "pause must discard the queued tail"
            assert not service.snapshot()["messages"][-1]["streaming"]
            assert service.snapshot()["phase"] == "paused"
        finally:
            service.shutdown()
            await runner
    asyncio.run(check())


def test_slow_playback_applies_backpressure_and_pause_discards_the_queue(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        session = Session(["hello"])
        def slow_say(text, timeout=60):
            session.played.append(text)
            session.cancelled.wait(3)
            raise RuntimeError("cancelled")
        session.say = slow_say
        robot.speech.open = lambda: session

        class FastAgent(Agent):
            sent = 0
            async def ask(self, text, *, on_text):
                for _ in range(200):
                    await on_text("这是需要逐句播报的内容。")
                    self.sent += 1
                return "这是需要逐句播报的内容。" * 200

        agent = FastAgent()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=lambda: agent)
        service.start()
        runner = asyncio.create_task(service.run())
        try:
            await wait_until(lambda: session.played)
            await asyncio.sleep(.1)
            assert agent.sent < 30, "slow TTS must bound pending generated segments"
            service.pause()
            await wait_until(lambda: session.closed and agent.closed)
            assert len(session.played) == 1
        finally:
            service.shutdown()
            await runner
    asyncio.run(check())


def test_session_rotates_at_request_budget_after_previous_audio_drains(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c)
        service.start()
        for _ in range(33):
            await service._speech_call(service.generation, "say", text="test")
        assert len(robot.sessions) == 2
        assert robot.sessions[0].closed
        assert len(robot.sessions[0].played) == 32
        assert robot.sessions[1].played == ["test"]
        assert not robot.sessions[1].closed
        await service._close_session()
    asyncio.run(check())


def test_streamed_sentence_whitespace_is_displayed_but_not_sent_to_tts(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        class SpacedAgent(Agent):
            async def ask(self, text, *, on_text):
                await on_text("第一句。 第二句。\n最后一句。")
                return "第一句。 第二句。\n最后一句。"
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=SpacedAgent)
        service.start()
        try:
            await service._reply(service.generation, "hello")
            assert robot.sessions[0].played == ["第一句。", "第二句。", "最后一句。"]
            assert service.messages[-1]["text"] == "第一句。 第二句。\n最后一句。"
        finally:
            await service._close_session()
            if service.agent:
                await service.agent.close()
    asyncio.run(check())


def test_failed_cleanup_is_retried_before_a_new_operation(monkeypatch):
    import pytest
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c)
        old = Session([])
        service.session = old
        attempts = []
        def flaky_close():
            attempts.append(True)
            if len(attempts) == 1:
                raise TimeoutError("cleanup pending")
            old.closed = True
        old.close = flaky_close
        with pytest.raises(TimeoutError):
            await service._close_session()
        service.start()
        await service._speech_call(service.generation, "say", text="new request")
        assert len(attempts) == 2 and old.closed
        assert len(robot.sessions) == 1
        assert old.played == []
        await service._close_session()
    asyncio.run(check())


def test_shutdown_retires_pi_even_when_device_cleanup_fails(monkeypatch):
    import pytest
    module = load(monkeypatch)

    async def check():
        robot, agent = Robot(), Agent()
        def failed_close():
            raise TimeoutError("cleanup pending")
        robot.speech.close = failed_close
        c = SimpleNamespace(robot=robot, shutdown_requested=True, logger=logging.getLogger("test"))
        service = module.ConversationService(c)
        service.agent = agent
        with pytest.raises(TimeoutError):
            await service.run()
        assert agent.closed
        assert service.cleanup_pending
    asyncio.run(check())


def test_pi_timeout_after_first_sentence_is_reported_as_pi_failure(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        class ExpiringAgent(Agent):
            async def ask(self, text, *, on_text):
                await on_text("先说一句。")
                await wait_until(lambda: robot.sessions[0].played)
                raise TimeoutError("Pi expired")
        agent = ExpiringAgent()
        c = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(c, agent_factory=lambda: agent)
        service.start()
        runner = asyncio.create_task(service.run())
        try:
            await wait_until(lambda: not service.enabled)
            assert "Pi" in service.error and "超时" in service.error
            assert service.turns == 0
            assert agent.closed and robot.sessions[0].closed
        finally:
            service.shutdown()
            await runner
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


def test_open_timeout_stops_and_keeps_actionable_error(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()
        opens = []

        def failed_open():
            opens.append(True)
            raise TimeoutError()

        robot.speech.open = failed_open
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context, agent_factory=Agent)
        service.start()
        runner = asyncio.create_task(service.run())
        try:
            await wait_until(lambda: not service.enabled)
            await asyncio.sleep(.15)
            state = service.snapshot()
            assert state["phase"] == "error"
            assert "启动" in state["error"] and "超时" in state["error"]
            assert len(opens) == 1
        finally:
            service.shutdown()
            await runner

    asyncio.run(check())


def test_cleanup_timeout_remains_visible_in_standby(monkeypatch):
    module = load(monkeypatch)

    async def check():
        robot = Robot()

        def failed_close():
            raise TimeoutError()

        def rejected_open():
            raise RuntimeError("open failed")

        robot.speech.open = rejected_open
        robot.speech.close = failed_close
        context = SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger("test"))
        service = module.ConversationService(context, agent_factory=Agent)
        service.start()
        runner = asyncio.create_task(service.run())
        try:
            await wait_until(lambda: not service.enabled)
            await asyncio.sleep(.15)
            assert service.snapshot()["error"]
            assert service.snapshot()["phase"] == "error"
        finally:
            robot.speech.close = lambda: None
            service.shutdown()
            await runner

    asyncio.run(check())
