"""提醒语音合成：edge-tts 在线合成 + ffmpeg 转码，失败时回退内置音频。

设备喇叭只接受 24 kHz 单声道 16-bit PCM WAV（见 SDK 的 ``audio.py``）。
edge-tts 7.x 只输出 MP3，因此合成后用 ffmpeg 转成目标格式：
``ffmpeg -i in.mp3 -ar 24000 -ac 1 -c:a pcm_s16le out.wav``。

两个外部工具都可选：任一缺失或某个步骤失败时，调用方降级到随应用内置的
示例 WAV，保证定时播报链路始终可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger("reminder.speech")

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
SAMPLE_RATE_HZ = 24000

# 合成器协议：(text, out_wav) -> 是否成功写出 WAV。可注入以便测试。
Synthesizer = Callable[[str, Path], Awaitable[bool]]


def edge_tts_available() -> bool:
    """当前 Python 环境能否 import edge-tts。"""
    return importlib.util.find_spec("edge_tts") is not None


def ffmpeg_available(ffmpeg: str = "ffmpeg") -> bool:
    """PATH 里能否找到 ffmpeg。"""
    return shutil.which(ffmpeg) is not None


async def synthesize_text_to_wav(
    text: str,
    out_wav: Path,
    *,
    voice: str = DEFAULT_VOICE,
    ffmpeg: str = "ffmpeg",
    python: str | None = None,
) -> bool:
    """把 ``text`` 合成为 ``out_wav``（24 kHz 单声道 16-bit PCM WAV）。

    使用 ``python -m edge_tts`` 拉取语音（MP3），再用 ``ffmpeg`` 转码为设备
    喇叭要求的精确格式。任何一步失败都返回 ``False`` 并记日志，让调用方
    降级到内置音频，而不中断常驻调度循环。
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    mp3_path = out_wav.with_suffix(".mp3")
    log_path = out_wav.with_suffix(".log")
    python_exe = python or sys.executable
    try:
        with log_path.open("wb") as log:
            synth = await asyncio.create_subprocess_exec(
                python_exe,
                "-m",
                "edge_tts",
                "--text",
                text,
                "--voice",
                voice,
                "--write-media",
                str(mp3_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=log,
            )
            await synth.wait()
        if synth.returncode != 0:
            logger.warning(
                "edge-tts 合成失败 (exit=%s)，详见 %s",
                synth.returncode,
                log_path,
            )
            return False

        convert = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(mp3_path),
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await convert.wait()
        if convert.returncode != 0:
            logger.warning("ffmpeg 转码失败 (exit=%s)", convert.returncode)
            out_wav.unlink(missing_ok=True)
            return False
        if not out_wav.exists() or out_wav.stat().st_size == 0:
            logger.warning("转换产物缺失或为空：%s", out_wav)
            out_wav.unlink(missing_ok=True)
            return False
        return True
    except OSError as exc:
        logger.warning("语音合成异常：%s", exc)
        return False
    finally:
        mp3_path.unlink(missing_ok=True)


class ReminderSpeech:
    """按文案缓存合成结果的播放源选择器。

    ``wav_for(text)`` 依次尝试：缓存命中 → 现场合成 → 内置示例音频。
    """

    def __init__(
        self,
        cache_dir: Path,
        fallback_wav: Path,
        *,
        voice: str = DEFAULT_VOICE,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.fallback_wav = Path(fallback_wav)
        self.voice = voice
        self._synthesizer = synthesizer or synthesize_text_to_wav

    def cache_path_for(self, text: str) -> Path:
        """同一文案永远命中同一缓存文件（按文案 sha256 前 16 位命名）。"""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"reminder-{digest}.wav"

    async def wav_for(self, text: str) -> Path:
        """返回可播放的 WAV 路径：缓存 → 合成 → 降级。"""
        cached = self.cache_path_for(text)
        if cached.exists() and cached.stat().st_size > 0:
            return cached
        if await self._synthesizer(text, cached):
            return cached
        logger.warning("无法为 %r 合成语音，回退到内置示例音频", text)
        return self.fallback_wav