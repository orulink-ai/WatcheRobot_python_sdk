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

    async def ask(self, text):
        self.prompts.append(text)
        return "reply " + str(len(self.prompts))

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
        await wait_until(lambda: robot.sessions[2].closed)
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
