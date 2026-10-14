"""Continuous conversation owner and loopback web API, independent of the UI."""
from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent import PiAgent, speech_chunks


class Interrupted(Exception):
    """A pause or shutdown invalidated the current turn."""


class ConversationService:
    def __init__(self, context: Any, *, agent_factory: Callable[..., Any] | None = None) -> None:
        self.context = context
        self.agent_factory = agent_factory or (lambda: PiAgent(cwd=os.environ.get("PI_WORKDIR")))
        self.enabled = False
        self.stopping = False
        self.phase = "paused"
        self.connected = False
        self.error = ""
        self.turns = 0
        self.generation = 0
        self.messages: deque[dict[str, Any]] = deque(maxlen=80)
        self.message_id = 0
        self.session: Any = None
        self.agent: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "phase": self.phase, "connected": self.connected,
            "error": self.error, "turns": self.turns,
            "device_id": self.context.robot.device_info.get("device_id", ""),
            "messages": list(self.messages),
        }

    def start(self) -> None:
        if self.stopping:
            raise RuntimeError("Application is shutting down")
        if not self.enabled:
            self.generation += 1
            self.enabled = True
            self.error = ""
            self.phase = "connecting"

    def pause(self) -> None:
        self.enabled = False
        self.generation += 1
        self.phase = "stopping" if self.session else "paused"

    def shutdown(self) -> None:
        self.stopping = True
        self.pause()

    def _alive(self, generation: int) -> bool:
        return (self.enabled and not self.stopping and not self.context.shutdown_requested
                and self.generation == generation)

    def _message(self, role: str, text: str) -> None:
        self.message_id += 1
        self.messages.append({"id": self.message_id, "role": role, "text": text})

    async def _cancel_capture(self) -> None:
        if self.session:
            try:
                await asyncio.to_thread(self.session.cancel)
            except Exception:
                # A failed cancellation must not leave a recording owner alive.
                await asyncio.to_thread(self.session.close)

    async def _call(self, generation: int, function: Callable[..., Any], **kwargs: Any) -> Any:
        task = asyncio.create_task(asyncio.to_thread(function, **kwargs))
        try:
            while not task.done():
                if not self._alive(generation):
                    await self._cancel_capture()
                    await asyncio.gather(task, return_exceptions=True)
                    raise Interrupted
                await asyncio.sleep(.05)
            result = await task
            if not self._alive(generation):
                raise Interrupted
            return result
        except asyncio.CancelledError:
            await self._cancel_capture()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _ask(self, generation: int, text: str) -> str:
        if self.agent is None:
            self.agent = await self.agent_factory().__aenter__()
        task = asyncio.create_task(self.agent.ask(
            "这是机器人语音输入。请用简短中文纯文本回答，方便语音播报。\n" + text))
        try:
            while not task.done():
                if not self._alive(generation):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await self.agent.close()
                    self.agent = None
                    raise Interrupted
                await asyncio.sleep(.05)
            reply = await task
            if not self._alive(generation):
                raise Interrupted
            return reply
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _close_session(self) -> None:
        try:
            # Also retire a timed-out open retained by the SDK domain.
            await asyncio.to_thread(self.session.close if self.session else self.context.robot.speech.close)
        finally:
            self.session = None

    async def run(self) -> None:
        next_refresh = 0.0
        try:
            while not self.stopping and not self.context.shutdown_requested:
                now = asyncio.get_running_loop().time()
                if now >= next_refresh:
                    try:
                        await asyncio.to_thread(self.context.robot.refresh_device_info, timeout=1)
                        self.connected = True
                    except Exception:
                        self.connected = False
                    next_refresh = now + 2
                if not self.enabled:
                    self.phase = "paused"
                    await asyncio.sleep(.1)
                    continue
                generation = self.generation
                self.error = ""
                self.phase = "connecting"
                try:
                    self.session = await asyncio.to_thread(self.context.robot.speech.open)
                    if not self._alive(generation):
                        raise Interrupted
                    test_text = os.environ.get("WATCHER_SPEECH_TEST_TEXT")
                    if test_text:
                        self.phase = "speaking"
                        await self._call(generation, self.session.say, text=test_text)
                        self.context.logger.info("TTS physical playback completed")
                        self.pause()
                        continue
                    self.phase = "listening"
                    self.context.logger.info("正在通过机器人麦克风识别（conversation_asr）")
                    text = await self._call(generation, self.session.recognize, timeout=30)
                    self._message("user", text)
                    self.context.logger.info("识别完成：%s", text)
                    self.phase = "thinking"
                    reply = ("你说的是：" + text if os.environ.get("WATCHER_SPEECH_AGENT") == "echo"
                             else await self._ask(generation, text))
                    if not self._alive(generation):
                        raise Interrupted
                    self._message("assistant", reply)
                    self.context.logger.info("Pi 回复：%s", reply)
                    self.phase = "speaking"
                    for chunk in speech_chunks(reply):
                        if chunk.strip():
                            await self._call(generation, self.session.say, text=chunk)
                    self.turns += 1
                    self.context.logger.info("Agent reply playback completed")
                    if os.environ.get("WATCHER_SPEECH_ONCE") == "1":
                        self.pause()
                except Interrupted:
                    pass
                except TimeoutError:
                    # Silence is ordinary; re-arm rather than stopping conversation.
                    self.phase = "connecting"
                except Exception as error:
                    self.error = str(error)
                    self.phase = "error"
                    self.context.logger.exception("语音轮次失败，清理后重试")
                    if self.agent:
                        await self.agent.close()
                        self.agent = None
                finally:
                    try:
                        await self._close_session()
                    except Exception as error:
                        self.error = str(error)
                        self.phase = "error"
                        self.enabled = False
                        self.context.logger.exception("语音资源释放失败，请暂停后重试")
                    if not self.enabled and not self.error:
                        self.phase = "paused"
                if self.error:
                    await asyncio.sleep(1)
        finally:
            self.enabled = False
            await self._close_session()
            if self.agent:
                await self.agent.close()
                self.agent = None
            self.phase = "paused"

    async def desktop_controls(self) -> None:
        while not self.stopping and not self.context.shutdown_requested:
            try:
                frame = await self.context.desktop.receive(timeout=.25)
            except TimeoutError:
                continue
            try:
                message = json.loads(frame) if isinstance(frame, str) else None
            except ValueError:
                continue
            if isinstance(message, dict):
                if message.get("type") == "ctrl.microphone.open":
                    self.start()
                elif message.get("type") == "ctrl.microphone.close":
                    self.pause()


def create_web_app(service: ConversationService, web_root: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_origin(request: Any, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        if (request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}
                or (origin and origin != str(request.base_url).rstrip("/"))
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"error": "仅允许本机 Application 页面访问"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.get("/")
    async def index() -> Any:
        return FileResponse(web_root / "index.html")

    @app.get("/api/state")
    async def state() -> Any:
        return service.snapshot()

    @app.post("/api/start")
    async def start() -> Any:
        service.start()
        return service.snapshot()

    @app.post("/api/pause")
    async def pause() -> Any:
        service.pause()
        return service.snapshot()

    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    return app
