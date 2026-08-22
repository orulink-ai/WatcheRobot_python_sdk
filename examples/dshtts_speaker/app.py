"""DSH TTS Speaker — DSH 对话文字经 edge-tts 转 PCM 后由 WatcheRobot 播放。"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import edge_tts
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from watcherobot.application import ApplicationContext

HTTP_HOST = os.environ.get("DSHTTS_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("DSHTTS_PORT", "9876"))
TTS_VOICE = os.environ.get("DSHTTS_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_RATE = os.environ.get("DSHTTS_RATE", "+0%")
MAX_TEXT_CHARS = 4000
MAX_PCM_BYTES = 4 * 1024 * 1024
VOICE_CATALOG = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 (女, 温柔)"},
    {"id": "zh-CN-YunxiNeural", "label": "云希 (男, 年轻)"},
    {"id": "zh-CN-YunjianNeural", "label": "云健 (男, 运动)"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 (女, 活泼)"},
    {"id": "zh-CN-YunyangNeural", "label": "云扬 (男, 新闻)"},
    {"id": "en-US-AriaNeural", "label": "Aria (English, female)"},
    {"id": "en-US-GuyNeural", "label": "Guy (English, male)"},
]
VOICE_IDS = {item["id"] for item in VOICE_CATALOG}
_app: ApplicationContext | None = None
_queue: asyncio.Queue["SpeechRequest"] | None = None
_worker: asyncio.Task[None] | None = None
_current_playback: Any = None
_stop_generation = 0
_config_lock = asyncio.Lock()


@dataclass
class SpeechRequest:
    text: str
    voice: str
    rate: str
    generation: int


def clean_text(text: str) -> str:
    """Remove Markdown/UI noise while preserving Chinese, English and numbers."""
    text = re.sub(r"```(?:[\w+-]+)?\s*[\r\n]([\s\S]*?)```", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT_CHARS]


async def _text_to_pcm(text: str, *, voice: str, rate: str) -> bytes:
    """edge-tts -> ffmpeg -> PCM. Retry Chinese voices for transient Edge failures."""
    candidates = [voice]
    if voice.startswith("zh-"):
        candidates += ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural"]
    last_error: Exception | None = None
    for candidate in dict.fromkeys(candidates):
        mp3_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                mp3_path = tmp.name
            await asyncio.wait_for(edge_tts.Communicate(text, candidate, rate=rate).save(mp3_path), 45)
            if Path(mp3_path).stat().st_size == 0:
                raise RuntimeError("edge-tts returned an empty audio file")
            result = await asyncio.to_thread(subprocess.run, [
                "ffmpeg", "-y", "-i", mp3_path, "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "24000", "-ac", "1", "-", ], capture_output=True, check=True, timeout=30)
            if result.stdout:
                return result.stdout
            raise RuntimeError("ffmpeg produced empty PCM")
        except Exception as exc:
            last_error = exc
        finally:
            if mp3_path:
                Path(mp3_path).unlink(missing_ok=True)
    raise RuntimeError(f"all TTS voices failed: {last_error}")


async def _play(req: SpeechRequest) -> None:
    global _current_playback
    if _app is None:
        raise RuntimeError("robot not connected")
    pcm = await _text_to_pcm(req.text, voice=req.voice, rate=req.rate)
    if len(pcm) > MAX_PCM_BYTES:
        pcm = pcm[:MAX_PCM_BYTES]
    if "audio.stream" not in _app.robot.capabilities:
        raise RuntimeError("robot firmware does not support audio.stream")
    try:
        await asyncio.to_thread(_app.robot.animation.play, "speaking")
    except Exception:
        pass
    try:
        _current_playback = await asyncio.to_thread(_app.robot.audio.play_pcm, pcm)
        await asyncio.to_thread(_current_playback.wait, 120.0)
    finally:
        _current_playback = None
        try:
            await asyncio.to_thread(_app.robot.animation.play, "standby")
        except Exception:
            pass


async def _worker_loop() -> None:
    global _stop_generation
    assert _queue is not None
    while True:
        req = await _queue.get()
        try:
            if req.generation == _stop_generation:
                await _play(req)
                if _app:
                    _app.logger.info("Spoke: %s", req.text[:100])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _app:
                _app.logger.error("speech failed: %s", exc)
        finally:
            _queue.task_done()


async def _ensure_worker() -> None:
    global _queue, _worker
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_worker_loop())


fastapi_app = FastAPI(title="DSH TTS Speaker", version="0.2.0")


@fastapi_app.post("/speak")
async def speak(request: Request) -> JSONResponse:
    body: dict[str, Any]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    text = clean_text(str(body.get("text", "")))
    if not text:
        return JSONResponse({"ok": False, "error": "text is required after Markdown cleanup"}, status_code=400)
    if _app is None:
        return JSONResponse({"ok": False, "error": "robot not connected"}, status_code=503)
    voice = str(body.get("voice", TTS_VOICE))
    rate = str(body.get("rate", TTS_RATE))
    await _ensure_worker()
    assert _queue is not None
    await _queue.put(SpeechRequest(text, voice, rate, _stop_generation))
    return JSONResponse({"ok": True, "queued": True, "text": text, "voice": voice, "queue_size": _queue.qsize()})


@fastapi_app.post("/stop")
async def stop() -> JSONResponse:
    global _stop_generation
    _stop_generation += 1
    if _queue:
        while not _queue.empty():
            try:
                _queue.get_nowait()
                _queue.task_done()
            except asyncio.QueueEmpty:
                break
    if _app is not None:
        try:
            await asyncio.to_thread(_app.robot.audio.stop)
        except Exception:
            pass
        try:
            await asyncio.to_thread(_app.robot.animation.play, "standby")
        except Exception:
            pass
    return JSONResponse({"ok": True, "stopped": True})


@fastapi_app.post("/settings")
async def settings(request: Request) -> JSONResponse:
    global TTS_VOICE, TTS_RATE
    body = await request.json()
    voice = str(body.get("voice", TTS_VOICE))
    rate = str(body.get("rate", TTS_RATE))
    if voice not in VOICE_IDS:
        return JSONResponse({"ok": False, "error": "unsupported voice", "voices": sorted(VOICE_IDS)}, status_code=400)
    async with _config_lock:
        TTS_VOICE, TTS_RATE = voice, rate
    return JSONResponse({"ok": True, "voice": TTS_VOICE, "rate": TTS_RATE})


@fastapi_app.get("/health")
async def health() -> JSONResponse:
    if _app is None:
        return JSONResponse({"ok": True, "robot_connected": False})
    return JSONResponse({"ok": True, "robot_connected": True, "capabilities": list(_app.robot.capabilities), "device_info": _app.robot.device_info, "queue_size": _queue.qsize() if _queue else 0, "voice": TTS_VOICE})


@fastapi_app.get("/voices")
async def voices() -> JSONResponse:
    return JSONResponse({"ok": True, "default": TTS_VOICE, "rate": TTS_RATE, "voices": VOICE_CATALOG})


async def main() -> None:
    global _app
    async with ApplicationContext.from_environment() as app:
        _app = app
        await _ensure_worker()
        app.logger.info("DSH TTS Speaker started on http://%s:%d", HTTP_HOST, HTTP_PORT)
        server = uvicorn.Server(uvicorn.Config(fastapi_app, host=HTTP_HOST, port=HTTP_PORT, log_level="info", access_log=False))
        await server.serve()


asyncio.run(main())
