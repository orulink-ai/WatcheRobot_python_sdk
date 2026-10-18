"""Speech choices for Pi; the Application owns cloud services and robot media I/O."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable
from contextlib import aclosing
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

import httpx

from watcherobot.audio import MAX_AUDIO_BYTES
from watcherobot.voice.configuration import ConversationConfig, ModelConfig, default_credential_directory
from watcherobot.voice.contracts import VoiceError
from watcherobot.voice.device import SDKVoiceDevice
from watcherobot.voice.providers.asr import StreamingASR
from watcherobot.voice.providers.http import CloudTTS

from tts_pipeline import run_tts_pipeline

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("watcherobot.pi.speech")


class JoyInsideBackend:
    name = "joyinside"
    label = "机器人 JoyInside"

    def __init__(self, robot: Any) -> None:
        self.robot = robot

    def snapshot(self) -> dict[str, Any]:
        return {"id": self.name, "label": self.label, "configured": True, "error": ""}

    def open(self) -> Any:
        return self.robot.speech.open()

    def close(self) -> None:
        self.robot.speech.close()


class _AcknowledgementCache:
    """One bounded, memory-only entry, never included in snapshots or logs."""

    def __init__(self) -> None:
        self.entry: tuple[bytes, str, bytes] | None = None


class DoubaoBackend:
    name = "doubao"
    label = "Application 豆包"

    def __init__(self, robot: Any, *, credentials_path: Path | None = None,
                 device: Any = None, asr: Any = None, tts: Any = None,
                 logger: Any = None) -> None:
        self.robot = robot
        self.logger = logger or LOGGER
        self.credentials_path = credentials_path or (
            default_credential_directory(ROOT, "com.orulink.joyinside_pi", installed=True) / "doubao.json")
        self._injected_device, self._injected_asr, self._injected_tts = device, asr, tts
        self._session: DoubaoSession | None = None
        self._tts_client: httpx.AsyncClient | None = None
        self._shutdown_started = False
        self._acknowledgement_cache = _AcknowledgementCache()

    def _configuration(self) -> tuple[ModelConfig, ModelConfig]:
        # Never include file contents, credential values, or JSON parser errors in public errors.
        try:
            if self.credentials_path.is_symlink() or self.credentials_path.parent.is_symlink():
                raise ValueError
            if self.credentials_path.stat().st_size > 16384:
                raise ValueError
            data = json.loads(self.credentials_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError
            values = {}
            for name, default in (("app_id", ""), ("access_token", ""),
                                  ("asr_resource_id", "volc.bigasr.sauc.duration"),
                                  ("tts_resource_id", "seed-tts-2.0"),
                                  ("voice_id", "zh_female_vv_uranus_bigtts")):
                value = data.get(name, default)
                if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                    raise ValueError
                values[name] = value.strip()
        except (OSError, ValueError, UnicodeError):
            raise VoiceError("豆包语音尚未配置或配置无效，请检查本机私有凭据文件") from None
        credentials = {name: values[name] for name in ("app_id", "access_token")}
        return (ModelConfig("volcengine", resource_id=values["asr_resource_id"], credentials=credentials),
                ModelConfig("volcengine", resource_id=values["tts_resource_id"],
                            voice_id=values["voice_id"], credentials=credentials))

    def snapshot(self) -> dict[str, Any]:
        try:
            self._configuration()
        except VoiceError as error:
            return {"id": self.name, "label": self.label, "configured": False, "error": str(error)}
        return {"id": self.name, "label": self.label, "configured": True, "error": ""}

    async def open(self) -> DoubaoSession:
        if self._shutdown_started:
            raise VoiceError("豆包语音后端已关闭或正在释放资源")
        if self._session is not None:
            if not self._session.closed:
                raise VoiceError("上一轮豆包语音资源尚未释放，请先暂停并重试清理")
            self._session = None
        asr_config, tts_config = self._configuration()
        # A digest binds the recording to every synthesis option and credential
        # without retaining credential values in the cache key.
        tts_fingerprint = hashlib.sha256(json.dumps(
            asdict(tts_config), sort_keys=True, ensure_ascii=False).encode("utf-8")).digest()
        device = self._injected_device or SDKVoiceDevice(self.robot, ConversationConfig())
        asr = self._injected_asr or StreamingASR(asr_config)
        # Retain providers before readiness checks so a failed open can be cleaned up.
        self._session = DoubaoSession(device, asr, self._injected_tts,
                                      asr_config.timeout, tts_config.timeout, logger=self.logger,
                                      acknowledgement_cache=self._acknowledgement_cache,
                                      tts_fingerprint=tts_fingerprint)
        try:
            if self._injected_tts is None:
                if self._tts_client is None:
                    # Keep one idle connection beyond playback and the next
                    # recording. No credentials or per-request headers live here.
                    self._tts_client = httpx.AsyncClient(
                        timeout=tts_config.timeout, follow_redirects=False,
                        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1,
                                            keepalive_expiry=60),
                    )
                self._session.tts = CloudTTS(tts_config, client=self._tts_client)
            if not await device.ready():
                raise VoiceError("机器人连接不可用，请先连接机器人")
            return self._session
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise VoiceError("豆包语音初始化失败，请检查机器人连接与本机配置") from None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def shutdown(self) -> None:
        """Release the active lease before retiring the shared TTS transport."""
        self._shutdown_started = True
        await self.close()
        if self._tts_client is not None:
            try:
                await self._tts_client.aclose()
            except Exception:
                # Retain the owner so a caller can retry failed transport cleanup.
                raise VoiceError("豆包语音连接资源释放未确认，请重试关闭") from None
            self._tts_client = None
        self._acknowledgement_cache.entry = None


class _UtteranceCapture:
    """One microphone owner drains continuously, independently of cloud upload.

    Keeping the bounded utterance in one byte buffer also bounds object overhead
    when an injected source happens to produce very small PCM frames.
    """

    def __init__(self, device: Any, limit: int) -> None:
        self.device, self.limit = device, limit
        self.audio = bytearray()
        self.first_ready, self.changed = asyncio.Event(), asyncio.Event()
        self.error: Exception | None = None
        self.complete = self.exhausted = False
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _UtteranceCapture:
        self.task = asyncio.create_task(self._produce())
        return self

    async def __aexit__(self, *args: Any) -> None:
        assert self.task is not None
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.check()

    async def _produce(self) -> None:
        try:
            # This task owns the source from its first anext through close.
            async with aclosing(self.device.utterance()) as source:
                async for pcm in source:
                    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
                        raise VoiceError("ASR 设备 PCM 数据无效")
                    if len(self.audio) + len(pcm) > self.limit:
                        raise VoiceError("ASR 单轮音频超过大小限制")
                    self.audio.extend(pcm)
                    self.first_ready.set()
                    self.changed.set()
                    await asyncio.sleep(0)
        except TimeoutError:
            # SDK read polling timeouts are handled inside utterance(). An
            # escaping timeout means opening/capturing/closing the device failed,
            # never the normal "no speech yet" deadline used by the service.
            self.error = VoiceError("ASR 设备采音或麦克风关闭超时，请检查机器人连接")
        except Exception as error:
            self.error = error
        finally:
            self.complete = True
            self.first_ready.set()
            self.changed.set()

    def check(self) -> None:
        if self.error is not None:
            raise self.error

    async def first(self) -> bool:
        await self.first_ready.wait()
        self.check()
        return bool(self.audio)

    async def frames(self) -> AsyncGenerator[bytes, None]:
        offset = 0
        while True:
            self.check()
            if offset < len(self.audio):
                end = min(offset + 3200, len(self.audio))
                pcm = bytes(self.audio[offset:end])
                offset = end
                yield pcm
            elif self.complete:
                self.exhausted = True
                return
            else:
                self.changed.clear()
                await self.changed.wait()


class DoubaoSession:
    """One half-duplex lease; cancel joins active work before touching device cleanup."""

    def __init__(self, device: Any, asr: Any, tts: Any, asr_timeout: float, tts_timeout: float,
                 *, logger: Any = None, acknowledgement_cache: _AcknowledgementCache | None = None,
                 tts_fingerprint: bytes = b"") -> None:
        self.device, self.asr, self.tts = device, asr, tts
        self.logger = logger or LOGGER
        self.asr_timeout, self.tts_timeout = asr_timeout, tts_timeout
        self._task: asyncio.Task[Any] | None = None
        self._closing = self.closed = False
        self._providers_closed: set[str] = set()
        self._conversation = ConversationConfig()
        self._acknowledgement_cache = acknowledgement_cache or _AcknowledgementCache()
        self._tts_fingerprint = tts_fingerprint
        self._cancel_generation = 0
        self._speech_kind = "direct"

    async def _run(self, operation: Callable[[], Awaitable[Any]], timeout: float | None = None) -> Any:
        if self._closing or self.closed:
            raise VoiceError("豆包语音会话已关闭或正在释放资源")
        if self._task is not None:
            raise VoiceError("已有豆包语音操作运行，请等待完成")
        task = asyncio.create_task(operation())
        self._task = task
        try:
            return await asyncio.wait_for(task, timeout)
        except (asyncio.CancelledError, TimeoutError):
            raise
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("豆包语音操作失败，请检查设备、网络与服务配置") from None
        finally:
            if self._task is task:
                self._task = None

    async def recognize(self, timeout: float = 30) -> str:
        return await self._run(lambda: self._recognize(timeout))

    async def _recognize(self, timeout: float) -> str:
        limit = int((self._conversation.max_utterance_seconds
                     + self._conversation.pre_roll_ms / 1000) * 32000) + 6400
        async with _UtteranceCapture(self.device, limit) as capture:
            # Start only the local capture owner. Silence never opens a cloud request.
            if not await asyncio.wait_for(capture.first(), timeout):
                raise TimeoutError("未检测到语音")

            async def transcribe() -> str | None:
                final_text: str | None = None
                async with aclosing(capture.frames()) as audio, aclosing(self.asr.transcribe(audio)) as results:
                    async for result in results:
                        if result.final:
                            if not isinstance(result.text, str) or len(result.text) > 20000:
                                raise VoiceError("ASR 最终识别文本无效或过长")
                            final_text = result.text.strip()
                capture.check()
                if not capture.exhausted:
                    raise VoiceError("ASR 未完整上传本轮录音，请重试")
                return final_text

            async def transcribe_until_capture_failure() -> str | None:
                operation = asyncio.create_task(transcribe())
                try:
                    # A capture failure must also abort a stalled cloud handshake.
                    await asyncio.wait((operation, capture.task), return_when=asyncio.FIRST_COMPLETED)
                    capture.check()
                    return await operation
                finally:
                    if not operation.done():
                        operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)

            try:
                # After speech starts, timeout means a service/capture failure, never silence.
                final_text = await asyncio.wait_for(transcribe_until_capture_failure(), timeout + self.asr_timeout)
            except TimeoutError:
                raise VoiceError("ASR 语音识别服务响应超时，请检查网络与服务配置") from None
            if final_text is None:
                raise VoiceError("ASR 未返回最终识别结果，请重试")
            if not final_text:
                raise TimeoutError("未检测到有效语音")
        # Exiting the utterance context acknowledges microphone close before Pi/TTS starts.
        return final_text

    async def say(self, text: str) -> None:
        await self._run(lambda: self._say(text))

    async def _say(self, text: str) -> None:
        self._speech_kind = "direct"
        await self._play_pcm(await self._synthesize(text, 1), 1)

    async def acknowledge(self, text: str) -> None:
        """Play a model-authored short preface; only this API caches matching audio."""
        await self._run(lambda: self._acknowledge(text))

    async def _acknowledge(self, text: str) -> None:
        self._speech_kind = "preface"
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 900:
            raise VoiceError("确认语必须是非空短文本")
        generation = self._cancel_generation

        def check() -> None:
            if self._closing or self.closed or generation != self._cancel_generation:
                raise asyncio.CancelledError

        entry = self._acknowledgement_cache.entry
        hit = entry is not None and entry[:2] == (self._tts_fingerprint, text)
        self.logger.info("speech_acknowledgement cache_hit=%s", hit)
        pcm = entry[2] if hit else await self._synthesize(text, 1)
        check()
        await self._play_pcm(pcm, 1)
        check()
        # Playback must also finish successfully before promoting new audio.
        # Ordinary answers never call this path or populate this one-entry cache.
        self._acknowledgement_cache.entry = (self._tts_fingerprint, text, pcm)

    async def say_segments(self, segments: Iterable[str]) -> None:
        """Generate ahead; capable devices play the whole reply on one stream."""
        await self._run(lambda: self._say_segments(segments))

    async def _say_segments(self, segments: Iterable[str]) -> None:
        self._speech_kind = "final"
        continuous = bool(getattr(self.device, "supports_continuous_playback", False))
        self.logger.info("speech_pipeline kind=final mode=%s", "continuous" if continuous else "legacy_batch")
        await run_tts_pipeline(
            segments, synthesize=self._synthesize, play_segment=self._play_pcm,
            play_stream=self._play_continuous if continuous else None, logger=self.logger,
        )

    async def _play_continuous(self, frames) -> None:
        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(self.device.play_stream(frames), MAX_AUDIO_BYTES / 48000 + self.tts_timeout + 20)
        except TimeoutError:
            await self.device.stop()
            raise VoiceError("机器人连续音频播放超时，请检查设备连接") from None
        finally:
            self._timing("continuous_end", started, 0)
            for name, value in getattr(self.device, "last_playback_stats", {}).items():
                if isinstance(value, (int, float)):
                    self.logger.info("speech_device kind=final metric=%s value=%s", name, value)

    def _timing(self, stage: str, started: float, segment: int) -> None:
        elapsed = round((asyncio.get_running_loop().time() - started) * 1000)
        self.logger.info("speech_timing stage=%s segment=%d elapsed_ms=%d kind=%s",
                         stage, segment, elapsed, self._speech_kind)

    async def _synthesize(self, text: str, segment: int) -> bytes:
        started = asyncio.get_running_loop().time()

        async def synthesize() -> bytes:
            pcm = bytearray()
            async with aclosing(self.tts.synthesize(text)) as chunks:
                async for chunk in chunks:
                    if (chunk.sample_rate, chunk.channels, chunk.encoding) != (24000, 1, "pcm_s16le"):
                        raise VoiceError("TTS 必须输出 24 kHz 单声道 S16LE PCM")
                    if not isinstance(chunk.data, bytes):
                        raise VoiceError("TTS PCM 数据无效")
                    if len(pcm) + len(chunk.data) > MAX_AUDIO_BYTES:
                        raise VoiceError("TTS 单段音频超过设备大小限制")
                    if not pcm and chunk.data:
                        self._timing("tts_first_audio", started, segment)
                    pcm.extend(chunk.data)
            if not pcm or len(pcm) % 2:
                raise VoiceError("TTS 返回空音频或不完整采样")
            return bytes(pcm)
        try:
            pcm = await asyncio.wait_for(synthesize(), self.tts_timeout)
        except TimeoutError:
            raise VoiceError("TTS 语音生成服务响应超时，请检查网络与服务配置") from None
        self._timing("tts_complete", started, segment)
        return pcm

    async def _play_pcm(self, pcm: bytes, segment: int) -> None:
        started = asyncio.get_running_loop().time()
        self._timing("playback_start", started, segment)
        try:
            await asyncio.wait_for(self.device.play(pcm), len(pcm) / 48000 + 20)
        except TimeoutError:
            await self.device.stop()
            raise VoiceError("机器人音频播放超时，请检查设备连接") from None
        except asyncio.CancelledError:
            await self.device.stop()
            raise
        self._timing("playback_complete", started, segment)

    async def cancel(self) -> None:
        self._cancel_generation += 1
        task = self._task
        errors = []
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)
            errors.extend(error for error in results
                          if isinstance(error, Exception) and not isinstance(error, TimeoutError))
        try:
            await self.device.stop()
        except Exception as error:
            errors.append(error)
        if errors:
            raise VoiceError("豆包语音资源释放未确认，请暂停后重试") from None

    async def close(self) -> None:
        if self.closed:
            return
        self._closing = True
        errors = []
        try:
            await self.cancel()
        except Exception as error:
            errors.append(error)
        for name in ("asr", "tts"):
            provider = getattr(self, name)
            if provider is None or name in self._providers_closed:
                continue
            try:
                await provider.close()
                self._providers_closed.add(name)
            except Exception as error:
                errors.append(error)
        if errors:
            raise VoiceError("豆包语音资源释放未确认，请暂停后重试") from None
        self.closed = True
