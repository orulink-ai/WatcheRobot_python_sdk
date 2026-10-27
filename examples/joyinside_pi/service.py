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

from agent import PiAgent
from sentences import SentenceBuffer


class Interrupted(Exception):
    """A pause or shutdown invalidated the current turn."""


class PiReplyTimeout(TimeoutError):
    """Distinguish generation timeouts from concurrent playback timeouts."""


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
        self.session_requests = 0
        self.revision = 0
        self.timings: dict[str, int] = {}
        self.operation = "connecting"
        self.playback_finished_at: float | None = None
        self.cleanup_pending = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "phase": self.phase, "connected": self.connected,
            "error": self.error, "turns": self.turns,
            "device_id": self.context.robot.device_info.get("device_id", ""),
            "messages": list(self.messages),
            "revision": self.revision, "timings": dict(self.timings),
            "cleanup_pending": self.cleanup_pending,
            "speech_supported": "speech.joyinside.v1" in getattr(self.context.robot, "capabilities", ()),
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

    def _message(self, role: str, text: str, *, streaming: bool = False) -> dict[str, Any]:
        self.message_id += 1
        message = {"id": self.message_id, "role": role, "text": text, "streaming": streaming}
        self.messages.append(message)
        self.revision += 1
        return message

    async def _cancel_capture(self) -> None:
        if self.session:
            try:
                await asyncio.to_thread(self.session.cancel)
            except Exception:
                # A failed cancellation must not leave a recording owner alive.
                await asyncio.to_thread(self.session.close)
        else:
            await asyncio.to_thread(self.context.robot.speech.close)

    async def _call(self, generation: int, function: Callable[..., Any], **kwargs: Any) -> Any:
        task = asyncio.create_task(asyncio.to_thread(function, **kwargs))
        try:
            while not task.done():
                if not self._alive(generation):
                    await self._cancel_capture()
                    await asyncio.gather(task, return_exceptions=True)
                    raise Interrupted
                await asyncio.wait((task,), timeout=.05)
            result = await task
            if not self._alive(generation):
                raise Interrupted
            return result
        except asyncio.CancelledError:
            await self._cancel_capture()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception:
            if not self._alive(generation):
                raise Interrupted from None
            raise

    async def _ask(self, generation: int, text: str, on_text: Callable[..., Any]) -> str:
        if self.agent is None:
            self.agent = await self.agent_factory().__aenter__()
        task = asyncio.create_task(self.agent.ask(
            "这是机器人语音输入。请用简短中文纯文本回答，方便语音播报。\n" + text,
            on_text=on_text))
        completed = False
        try:
            while not task.done():
                if not self._alive(generation):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise Interrupted
                await asyncio.wait((task,), timeout=.05)
            reply = await task
            if not self._alive(generation):
                raise Interrupted
            completed = True
            return reply
        except TimeoutError as error:
            raise PiReplyTimeout from error
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not completed and self.agent:
                await self.agent.close()
                self.agent = None

    async def _speech_call(self, generation: int, kind: str, **kwargs: Any) -> Any:
        if self.cleanup_pending:
            self.phase = self.operation = "connecting"
            await self._close_session()
        if self.session is None or self.session_requests >= 32:
            self.phase = self.operation = "connecting"
            if self.session is not None:
                await self._close_session()
            self.context.logger.info("启动机器人语音会话，设备=%s",
                                     self.context.robot.device_info.get("device_id", ""))
            self.session = await self._call(generation, self.context.robot.speech.open)
            self.session_requests = 0
        self.phase = self.operation = "listening" if kind == "recognize" else "speaking"
        self.session_requests += 1
        if kind == "recognize" and self.playback_finished_at is not None:
            self.timings["rearm_dispatch_ms"] = round((asyncio.get_running_loop().time() - self.playback_finished_at) * 1000)
            self.context.logger.info("播报完成到下一次聆听请求（ms）：%s", self.timings["rearm_dispatch_ms"])
            self.playback_finished_at = None
        return await self._call(generation, getattr(self.session, kind), **kwargs)

    async def _reply(self, generation: int, text: str) -> None:
        """Bounded producer -> sentence assembler -> single playback owner."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        raw: asyncio.Queue[str | None] = asyncio.Queue(maxsize=8)
        speech: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2)
        message = self._message("assistant", "", streaming=True)
        self.timings = {}

        def mark(name: str) -> None:
            self.timings.setdefault(name, round((loop.time() - started) * 1000))

        async def on_text(delta: str) -> None:
            if not self._alive(generation):
                raise Interrupted
            mark("pi_first_text_ms")
            message["text"] += delta
            self.revision += 1
            await raw.put(delta)

        async def generate() -> None:
            if os.environ.get("WATCHER_SPEECH_AGENT") == "echo":
                await on_text("你说的是：" + text)
            else:
                reply = await self._ask(generation, text, on_text)
                self.context.logger.info("Pi 回复：%s", reply)
            mark("pi_complete_ms")
            await raw.put(None)

        async def assemble() -> None:
            buffer = SentenceBuffer()
            while True:
                try:
                    delta = await asyncio.wait_for(raw.get(), .7)
                except asyncio.TimeoutError:
                    chunks = buffer.flush(idle=True)
                else:
                    chunks = buffer.flush() if delta is None else buffer.feed(delta)
                    if delta is None:
                        for chunk in chunks:
                            await speech.put(chunk)
                        await speech.put(None)
                        return
                for chunk in chunks:
                    await speech.put(chunk)

        async def play() -> None:
            while (chunk := await speech.get()) is not None:
                if not self._alive(generation):
                    raise Interrupted
                mark("first_tts_request_ms")
                # The cloud may trim sentence-leading whitespace in its
                # subtitle. Keep display text intact but make TTS correlation
                # compare the same normalized sentence.
                chunk = chunk.strip()
                self.context.logger.info("流式分句播报：%s", chunk)
                await self._speech_call(generation, "say", text=chunk)
            mark("playback_complete_ms")

        tasks = [asyncio.create_task(fn()) for fn in (generate, assemble, play)]
        completed = False
        try:
            await asyncio.gather(*tasks)
            if not self._alive(generation):
                raise Interrupted
            self.playback_finished_at = loop.time()
            completed = True
            self.context.logger.info("语音流水线耗时（自 ASR 完成起，ms）：%s", self.timings)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            message["streaming"] = False
            message["interrupted"] = not completed
            self.revision += 1

    async def _close_session(self) -> None:
        # Keep the owner and pending flag if cleanup fails. The next attempt
        # must finish this cleanup before opening or sending another request.
        self.cleanup_pending = True
        # Also retire a timed-out open retained by the SDK domain.
        await asyncio.to_thread(self.session.close if self.session else self.context.robot.speech.close)
        self.session = None
        self.session_requests = 0
        self.cleanup_pending = False

    async def run(self) -> None:
        next_refresh = 0.0
        try:
            while not self.stopping and not self.context.shutdown_requested:
                now = asyncio.get_running_loop().time()
                if now >= next_refresh and not (self.enabled and self.session is not None):
                    try:
                        await asyncio.to_thread(self.context.robot.refresh_device_info, timeout=1)
                        self.connected = True
                    except Exception:
                        self.connected = False
                    next_refresh = now + 2
                if not self.enabled:
                    self.phase = "error" if self.error else "paused"
                    await asyncio.sleep(.1)
                    continue
                generation = self.generation
                self.error = ""
                retire_session = False
                try:
                    test_text = os.environ.get("WATCHER_SPEECH_TEST_TEXT")
                    if test_text:
                        await self._speech_call(generation, "say", text=test_text)
                        self.context.logger.info("TTS physical playback completed")
                        self.pause()
                        continue
                    if self.agent is None and os.environ.get("WATCHER_SPEECH_AGENT") != "echo":
                        # Let Pi initialize while the device connects and the
                        # user speaks, instead of starting it after final ASR.
                        self.agent = await self.agent_factory().__aenter__()
                    self.context.logger.info("正在通过机器人麦克风识别（conversation_asr）")
                    text = await self._speech_call(generation, "recognize", timeout=30)
                    self._message("user", text)
                    self.context.logger.info("识别完成：%s", text)
                    self.phase = self.operation = "thinking"
                    await self._reply(generation, text)
                    self.turns += 1
                    self.context.logger.info("Agent reply playback completed")
                    if os.environ.get("WATCHER_SPEECH_ONCE") == "1":
                        self.pause()
                except Interrupted:
                    pass
                except TimeoutError as error:
                    if not isinstance(error, PiReplyTimeout) and self.operation == "listening":
                        # Silence is ordinary; re-arm rather than stopping conversation.
                        self.phase = "connecting"
                        retire_session = True
                    else:
                        if isinstance(error, PiReplyTimeout):
                            self.error = "Pi 回复超时，请检查 Pi 的模型连接后重新开始。"
                        else:
                            operation = {"connecting": "启动语音会话", "thinking": "Pi 回复",
                                         "speaking": "机器人播报"}.get(self.operation, "语音操作")
                            self.error = operation + "超时，请确认设备固件与 JoyInside 配置后重新开始。"
                        self.phase = "error"
                        self.enabled = False
                        self.context.logger.exception(self.error)
                        if self.agent:
                            await self.agent.close()
                            self.agent = None
                except Exception as error:
                    self.error = str(error) or type(error).__name__
                    self.phase = "error"
                    self.enabled = False
                    self.context.logger.exception("语音轮次失败，等待重新开始")
                    if self.agent:
                        await self.agent.close()
                        self.agent = None
                finally:
                    if retire_session or not self._alive(generation) or self.error:
                        try:
                            await self._close_session()
                        except Exception as error:
                            detail = str(error) or type(error).__name__
                            self.error = (self.error.rstrip("。") + "；" if self.error else "") + "语音资源释放失败：" + detail
                            self.phase = "error"
                            self.enabled = False
                            self.context.logger.exception("语音资源释放失败，请暂停后重试")
                    if not self.enabled and not self.error:
                        self.phase = "paused"
                if self.error:
                    await asyncio.sleep(1)
        finally:
            self.enabled = False
            try:
                await self._close_session()
            finally:
                if self.agent:
                    await self.agent.close()
                    self.agent = None
                self.phase = "error" if self.cleanup_pending or self.error else "paused"

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
