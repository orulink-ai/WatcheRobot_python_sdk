"""Continuous conversation owner and loopback web API, independent of the UI."""
from __future__ import annotations

import asyncio
import json
import inspect
import os
from collections import deque
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request as AnyRequest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent import PiAgent, speech_chunks
from connection import ConnectionError, DaemonConnection, validate_pairing_code
from streaming_text import TextSpeechPreview


class Interrupted(Exception):
    """A pause or shutdown invalidated the current turn."""


class ConversationService:
    def __init__(self, context: Any, *, agent_factory: Callable[..., Any] | None = None,
                 speech_backends: dict[str, Any] | None = None, backend_name: str = "joyinside",
                 save_backend: Callable[[str], None] | None = None, behavior: Any = None) -> None:
        self.context = context
        if speech_backends is None:
            from speech_backends import DoubaoBackend, JoyInsideBackend
            speech_backends = {"joyinside": JoyInsideBackend(context.robot),
                               "doubao": DoubaoBackend(context.robot, logger=getattr(context, "logger", None))}
        self.speech_backends = speech_backends
        if backend_name not in self.speech_backends:
            raise ValueError("无效的语音后端")
        self.backend_name = backend_name
        self.save_backend = save_backend
        if behavior is None:
            from behavior import RobotBehavior
            behavior = RobotBehavior(context.robot)
        self.behavior = behavior
        self.timings_ms: dict[str, int] = {}
        self.agent_open_task: asyncio.Task | None = None
        self.agent_factory = agent_factory or (lambda: PiAgent(cwd=os.environ.get("PI_WORKDIR")))
        self.voice_instructions = Path(__file__).with_name("voice_prompt.md").read_text(encoding="utf-8").strip()
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
        self.session_requests = 0
        self.agent: Any = None
        self.opening = False
        self.cleanup_pending = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "phase": self.phase, "connected": self.connected,
            "error": self.error, "turns": self.turns,
            "device_id": self.context.robot.device_info.get("device_id", ""),
            "messages": list(self.messages),
            "speech_backend": self.backend_name,
            "speech_backends": [backend.snapshot() for backend in self.speech_backends.values()],
            "behavior": self.behavior.snapshot(),
            "timings_ms": dict(self.timings_ms),
            "speech_switch_available": not (self.enabled or self.opening or self.session or self.cleanup_pending
                                                or self.behavior.snapshot().get("cleanup_pending")),
        }

    def select_backend(self, name: str) -> None:
        if name not in self.speech_backends:
            raise ConnectionError("请选择受支持的语音后端", 400)
        if (self.enabled or self.opening or self.session or self.cleanup_pending or self.stopping
                or self.behavior.snapshot().get("cleanup_pending")):
            raise ConnectionError("请先暂停对话并等待音频资源释放，再切换语音后端", 409)
        if not self.speech_backends[name].snapshot().get("configured"):
            raise ConnectionError("该语音后端尚未配置可用凭据", 400)
        if self.save_backend is not None:
            try:
                self.save_backend(name)
            except Exception:
                raise ConnectionError("无法保存语音后端设置，请检查本机文件权限", 503) from None
        self.backend_name = name
        self.error = ""
        self.phase = "paused"

    @staticmethod
    async def _invoke(function: Callable[..., Any], **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(function):
            return await function(**kwargs)
        result = await asyncio.to_thread(function, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def start(self) -> None:
        if self.stopping:
            raise RuntimeError("Application is shutting down")
        if not self.enabled and (self.cleanup_pending or self.behavior.snapshot().get("cleanup_pending")):
            raise ConnectionError("上一轮音频资源尚未释放，请停止并重启 Application 后重试", 409)
        if not self.enabled:
            self.generation += 1
            self.enabled = True
            self.error = ""
            self.phase = "connecting"

    def pause(self) -> None:
        self.enabled = False
        self.generation += 1
        self.phase = "stopping" if self.session or self.opening or self.behavior.snapshot().get("active") else ("error" if self.error else "paused")

    def shutdown(self) -> None:
        self.stopping = True
        self.pause()

    def _alive(self, generation: int) -> bool:
        return (self.enabled and not self.stopping and not self.context.shutdown_requested
                and self.generation == generation)

    def _message(self, role: str, text: str, *, kind: str = "reply") -> None:
        self.message_id += 1
        self.messages.append({"id": self.message_id, "role": role, "text": text, "kind": kind})

    async def _cancel_capture(self) -> None:
        if self.session:
            try:
                await self._invoke(self.session.cancel)
            except Exception:
                # A failed cancellation must not leave a recording owner alive.
                await self._invoke(self.session.close)

    async def _call(self, generation: int, function: Callable[..., Any], **kwargs: Any) -> Any:
        task = asyncio.create_task(self._invoke(function, **kwargs))
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

    def _reuse_session(self) -> bool:
        return bool(getattr(self.speech_backends[self.backend_name], "reuse_session", False)
                    and "speech.joyinside.v1" in getattr(self.context.robot, "capabilities", ()))

    async def _session_call(self, generation: int, method: str, **kwargs: Any) -> Any:
        if self._reuse_session() and self.session_requests >= 32:
            # The firmware retains a bounded request ledger. Rotate only after
            # the previous request completes, before sending the next one.
            await self._close_session()
            self.opening = True
            try:
                self.session = await self._invoke(self.speech_backends[self.backend_name].open)
            finally:
                self.opening = False
            if not self._alive(generation):
                raise Interrupted
        self.session_requests += 1
        return await self._call(generation, getattr(self.session, method), **kwargs)

    async def _open_agent(self) -> None:
        started = asyncio.get_running_loop().time()
        self.agent = await self.agent_factory().__aenter__()
        self._timing("pi_start", started)

    def _prewarm_agent(self) -> None:
        if self.agent is None and self.agent_open_task is None:
            self.agent_open_task = asyncio.create_task(self._open_agent())

    async def _close_agent(self) -> None:
        if self.agent_open_task is not None:
            await asyncio.gather(self.agent_open_task, return_exceptions=True)
            self.agent_open_task = None
        if self.agent is not None:
            await self.agent.close()
            self.agent = None

    def _timing(self, stage: str, started: float) -> None:
        elapsed = round((asyncio.get_running_loop().time() - started) * 1000)
        self.timings_ms[stage] = elapsed
        logger = getattr(self.context, "logger", None)
        if logger is not None:
            logger.info("voice_timing stage=%s elapsed_ms=%d", stage, elapsed)

    async def _phase(self, phase: str, *, text: str = "") -> None:
        self.phase = phase
        await self.behavior.set_phase("speaking" if phase == "acknowledging" else phase, text=text)

    async def _stop_behavior(self) -> None:
        try:
            await self.behavior.stop()
        except Exception:
            self.enabled = False
            self.error = (self.error + "；" if self.error else "") + "动作表情尚未释放，请检查机器人连接后重试"
            self.phase = "error"
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                logger.exception("动作表情清理未确认")

    async def _acknowledge(self, generation: int, text: str) -> None:
        started = asyncio.get_running_loop().time()
        if not self._alive(generation):
            raise Interrupted
        await self._phase("acknowledging", text=text)
        if not self._alive(generation):
            raise Interrupted
        self._message("assistant", text, kind="preface")
        # Pi authored the text; completing playback releases its waiting tool.
        acknowledge = "acknowledge" if hasattr(self.session, "acknowledge") else "say"
        try:
            await self._session_call(generation, acknowledge, text=text)
        except Interrupted:
            raise
        except Exception as error:
            raise RuntimeError(f"机器人操作前播报失败：{str(error) or type(error).__name__}") from error
        self._timing("acknowledgement", started)
        await self._phase("thinking")
        if not self._alive(generation):
            raise Interrupted

    async def _ask(self, generation: int, text: str, *, on_event: Callable | None = None,
                   before_speak: Callable | None = None) -> str:
        self._prewarm_agent()
        if self.agent_open_task is not None:
            await asyncio.shield(self.agent_open_task)
        if not self._alive(generation):
            raise Interrupted
        async def speak(spoken: str) -> None:
            if before_speak is not None:
                await before_speak()
            await self._acknowledge(generation, spoken)

        task = asyncio.create_task(self.agent.ask(
            self.voice_instructions + "\n\n用户语音识别原文：\n" + text,
            on_speak=speak,
            **({"on_event": on_event} if on_event is not None else {})))
        try:
            while not task.done():
                if not self._alive(generation):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await self._close_agent()
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
        self.cleanup_pending = True
        # Keep ownership on failure: switching must not bypass unconfirmed cleanup.
        await self._invoke(self.session.close if self.session else self.speech_backends[self.backend_name].close)
        self.session = None
        self.session_requests = 0
        self.cleanup_pending = False

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
                    if self.session is not None and not self.cleanup_pending:
                        try:
                            await self._close_session()
                        except Exception as error:
                            self.error = "语音资源释放失败：" + (str(error) or type(error).__name__)
                        await self._stop_behavior()
                    self.phase = "error" if self.error else "paused"
                    await asyncio.sleep(.1)
                    continue
                generation = self.generation
                self.error = ""
                self.phase = "connecting"
                operation = "机器人语音连接"
                preview = None
                retire_session = False
                try:
                    if not os.environ.get("WATCHER_SPEECH_TEST_TEXT") and os.environ.get("WATCHER_SPEECH_AGENT") != "echo":
                        self._prewarm_agent()
                    self.opening = True
                    try:
                        if self.session is None:
                            self.session = await self._invoke(self.speech_backends[self.backend_name].open)
                    finally:
                        self.opening = False
                    if not self._alive(generation):
                        raise Interrupted
                    if not self.behavior.snapshot().get("active"):
                        await self.behavior.start()
                    if not self._alive(generation):
                        raise Interrupted
                    test_text = os.environ.get("WATCHER_SPEECH_TEST_TEXT")
                    if test_text:
                        await self._phase("speaking", text=test_text)
                        operation = "机器人语音播报"
                        await self._session_call(generation, "say", text=test_text)
                        self.context.logger.info("TTS physical playback completed")
                        self.pause()
                        continue
                    await self._phase("listening")
                    recognition_started = asyncio.get_running_loop().time()
                    operation = "机器人语音识别"
                    self.context.logger.info("正在通过机器人麦克风识别（conversation_asr）")
                    try:
                        text = await self._session_call(generation, "recognize", timeout=30)
                    except TimeoutError:
                        # Only recognition keeps the existing silence/re-arm policy.
                        retire_session = True
                        continue
                    self._timing("recognition", recognition_started)
                    self._message("user", text)
                    self.behavior.user_cue(text)
                    await self._phase("thinking")
                    pi_started = asyncio.get_running_loop().time()
                    operation = "Pi 回复"
                    if (getattr(self.session, "supports_text_preview", False)
                            and os.environ.get("WATCHER_SPEECH_AGENT") != "echo"):
                        preview = TextSpeechPreview(self.session.say_segments, logger=self.context.logger)
                    reply = ("你说的是：" + text if os.environ.get("WATCHER_SPEECH_AGENT") == "echo"
                             else await self._ask(generation, text,
                                                  on_event=preview.handle if preview else None,
                                                  before_speak=preview.join if preview else None))
                    if not self._alive(generation):
                        raise Interrupted
                    self._timing("pi", pi_started)
                    self._message("assistant", reply)
                    await self._phase("speaking", text=reply)
                    speech_started = asyncio.get_running_loop().time()
                    operation = "机器人语音播报"
                    if preview is not None and await self._call(generation, preview.play, final=reply):
                        pass
                    elif hasattr(self.session, "say_segments"):
                        await self._session_call(generation, "say_segments", segments=speech_chunks(reply))
                    else:
                        for chunk in speech_chunks(reply):
                            if chunk.strip():
                                await self._session_call(generation, "say", text=chunk)
                    self._timing("speech", speech_started)
                    self.turns += 1
                    self.context.logger.info("Agent reply playback completed")
                    if os.environ.get("WATCHER_SPEECH_ONCE") == "1":
                        self.pause()
                except Interrupted:
                    pass
                except Exception as error:
                    reason = str(error) or type(error).__name__
                    self.error = f"{operation}失败：{reason}"
                    self.phase = "error"
                    self.enabled = False
                    self.context.logger.exception("语音轮次失败，请检查错误后重试")
                    await self._close_agent()
                finally:
                    try:
                        if preview is not None:
                            await preview.close()
                        if retire_session or not self._reuse_session() or not self._alive(generation) or self.error:
                            await self._close_session()
                    except Exception as error:
                        reason = str(error) or type(error).__name__
                        cleanup_error = f"语音资源释放失败：{reason}"
                        self.error = f"{self.error}；{cleanup_error}" if self.error else cleanup_error
                        self.phase = "error"
                        self.enabled = False
                        self.context.logger.exception("语音资源释放失败，请暂停后重试")
                    if not self.enabled:
                        await self._stop_behavior()
                    if not self.enabled and not self.error:
                        self.phase = "paused"
                if self.error:
                    await asyncio.sleep(1)
        finally:
            self.enabled = False
            try:
                await self._close_session()
            finally:
                await self._stop_behavior()
                try:
                    await self._close_agent()
                finally:
                    for backend in self.speech_backends.values():
                        shutdown = getattr(backend, "shutdown", None)
                        if shutdown is not None:
                            await self._invoke(shutdown)
            self.phase = "error" if self.error else "paused"

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
                    try:
                        self.start()
                    except ConnectionError as error:
                        self.error = str(error)
                        self.phase = "error"
                elif message.get("type") == "ctrl.microphone.close":
                    self.pause()


def create_web_app(service: ConversationService, web_root: Path,
                   *, connection: DaemonConnection | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    pairing_lock = asyncio.Lock()

    @app.exception_handler(ConnectionError)
    async def connection_error(_request: Any, error: ConnectionError) -> Any:
        payload = {"error": str(error)}
        if error.code is not None:
            payload["error_code"] = error.code
        return JSONResponse(payload, status_code=error.status_code)

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
        if pairing_lock.locked():
            return JSONResponse({"error": "正在发起配对，请等待机器人连接"}, status_code=409)
        async with pairing_lock:
            if connection is not None:
                status = await asyncio.to_thread(connection.status)
                if not status.get("online"):
                    raise ConnectionError("请先连接机器人，再开始对话", 409)
            service.start()
        return service.snapshot()

    @app.post("/api/pause")
    async def pause() -> Any:
        service.pause()
        return service.snapshot()

    @app.get("/api/connection")
    async def device_connection() -> Any:
        if connection is None:
            raise ConnectionError("当前运行环境未提供机器人连接服务")
        return await asyncio.to_thread(connection.status)

    @app.post("/api/pair", status_code=202)
    async def pair(request: AnyRequest) -> Any:
        try:
            body = await request.json()
        except ValueError:
            raise ConnectionError("请输入机器人屏幕上的六位数字配对码", 400) from None
        code = validate_pairing_code(body.get("pairing_code") if isinstance(body, dict) else None)
        if connection is None:
            raise ConnectionError("当前运行环境未提供机器人连接服务")
        if service.cleanup_pending and not (service.enabled or service.opening):
            raise ConnectionError("上一轮语音资源未释放，请重启 Pi Application 后重新配对", 409,
                                  code="speech_cleanup_required")
        if service.enabled or service.opening or service.session or service.cleanup_pending or pairing_lock.locked():
            raise ConnectionError("请先暂停对话并等待当前操作结束，再连接机器人", 409,
                                  code="conversation_busy")
        async with pairing_lock:
            return await asyncio.to_thread(connection.pair, code)

    @app.post("/api/speech/backend")
    async def select_speech_backend(request: AnyRequest) -> Any:
        try:
            body = await request.json()
        except ValueError:
            raise ConnectionError("请选择受支持的语音后端", 400) from None
        name = body.get("backend") if isinstance(body, dict) else None
        if not isinstance(name, str):
            raise ConnectionError("请选择受支持的语音后端", 400)
        if pairing_lock.locked():
            raise ConnectionError("正在连接机器人，请等待后再切换", 409)
        async with pairing_lock:
            service.select_backend(name)
        return service.snapshot()

    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    return app
