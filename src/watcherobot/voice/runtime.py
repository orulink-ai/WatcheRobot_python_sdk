"""Half-duplex conversations independent of provider and device protocols."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import aclosing, contextmanager
from typing import AsyncGenerator, AsyncIterator, Iterator, Protocol

from watcherobot.application import ApplicationContext
from watcherobot.audio import MAX_AUDIO_BYTES

from .configuration import VoiceConfiguration
from .contracts import ASR, LLM, TTS, Message, VoiceError
from .providers import ProviderRegistry


class VoiceDevice(Protocol):
    def utterance(self) -> AsyncGenerator[bytes, None]: ...
    async def play(self, pcm: bytes) -> None: ...
    async def stop(self) -> None: ...


class VoiceSession:
    def __init__(self, config: VoiceConfiguration, asr: ASR, llm: LLM, tts: TTS, device: VoiceDevice) -> None:
        self.config, self.asr, self.llm, self.tts, self.device = config, asr, llm, tts, device
        self.history: list[Message] = []
        self._generation = 0
        self._task: asyncio.Task[None] | None = None
        self.stage = 'idle'
        self.last_error_stage = ''

    def _check(self, generation: int) -> None:
        if generation != self._generation:
            raise asyncio.CancelledError

    @contextmanager
    def _error_stage(self, stage: str) -> Iterator[None]:
        """Attribute failures to their task, independently of concurrent progress."""
        self.stage = stage
        try:
            yield
        except Exception:
            if not self.last_error_stage:
                self.last_error_stage = stage
            raise

    async def cancel(self) -> None:
        self._generation += 1
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.device.stop()

    async def turn(self) -> None:
        if self._task is not None:
            raise RuntimeError('已有语音轮次运行')
        self._task = asyncio.current_task()
        generation = self._generation
        self.last_error_stage = ''
        try:
            self.stage = 'ASR'
            async def recognize() -> str:
                final_text = ''
                # Wait for actual speech before opening a cloud recognition session.
                async with aclosing(self.device.utterance()) as source:
                    first = await anext(source, None)
                    if first is None:
                        return ''
                    async def frames() -> AsyncGenerator[bytes, None]:
                        yield first
                        async for frame in source:
                            yield frame
                    async with aclosing(self.asr.transcribe(frames())) as results:
                        async for result in results:
                            self._check(generation)
                            if result.final:
                                final_text = result.text.strip()
                return final_text
            text = await asyncio.wait_for(recognize(), self.config.conversation.max_utterance_seconds + self.config.asr.timeout + 10)
            self._check(generation)
            if not text:
                return
            messages = [Message('system', self.config.prompt), *self.history, Message('user', text)]
            reply = await self._answer(messages, generation)
            self._check(generation)
            if reply and self.config.conversation.history_turns:
                self.history.extend([Message('user', text), Message('assistant', reply)])
                self.history = self.history[-2 * self.config.conversation.history_turns:]
        except asyncio.CancelledError:
            raise
        except VoiceError:
            self.last_error_stage = self.last_error_stage or self.stage
            raise
        except Exception:
            self.last_error_stage = self.last_error_stage or self.stage
            raise VoiceError(f'{self.last_error_stage} 阶段失败；本轮已清理，请检查连接与配置') from None
        finally:
            self.stage = 'idle'
            self._task = None

    async def _answer(self, messages: list[Message], generation: int) -> str:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2)
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        reply: list[str] = []
        stopping = False

        def check() -> None:
            self._check(generation)
            if stopping:
                raise asyncio.CancelledError

        cfg = self.config.conversation

        async def produce() -> None:
            pending = ''
            length = 0
            with self._error_stage('LLM'):
                async with aclosing(self.llm.generate(messages)) as stream:
                    while length < cfg.max_reply_characters:
                        try:
                            piece = await asyncio.wait_for(anext(stream), self.config.llm.timeout)
                        except StopAsyncIteration:
                            break
                        check()
                        piece = piece[:cfg.max_reply_characters - length]
                        length += len(piece)
                        reply.append(piece)
                        pending += piece
                        while pending:
                            match = re.search(r'[。！？!?\n]', pending[:cfg.segment_characters])
                            end = match.end() if match else cfg.segment_characters if len(pending) >= cfg.segment_characters else 0
                            if not end:
                                break
                            await queue.put(pending[:end])
                            pending = pending[end:]
                    if pending.strip():
                        await queue.put(pending)
                if not ''.join(reply).strip():
                    raise VoiceError('LLM 返回空回答')
                await queue.put(None)

        async def synthesize_segments() -> None:
            while True:
                segment = await queue.get()
                if segment is None:
                    await audio_queue.put(None)
                    return
                if not segment.strip():
                    continue
                check()
                self.stage = 'TTS'
                async def synthesize() -> bytes:
                    pcm = bytearray()
                    async with aclosing(self.tts.synthesize(segment)) as stream:
                        async for chunk in stream:
                            check()
                            if (chunk.sample_rate, chunk.channels, chunk.encoding) != (24000, 1, 'pcm_s16le'):
                                raise VoiceError('TTS 必须输出 24 kHz 单声道 S16LE PCM；自定义适配器须先转换格式')
                            if len(pcm) + len(chunk.data) > MAX_AUDIO_BYTES:
                                raise VoiceError('TTS 单段音频超过设备大小限制')
                            pcm.extend(chunk.data)
                    if not pcm or len(pcm) % 2:
                        raise VoiceError('TTS 返回空音频或不完整采样')
                    return bytes(pcm)
                with self._error_stage('TTS'):
                    pcm = await asyncio.wait_for(synthesize(), self.config.tts.timeout)
                check()
                await audio_queue.put(pcm)
                check()

        async def play_segments() -> None:
            while True:
                pcm = await audio_queue.get()
                if pcm is None:
                    return
                check()
                with self._error_stage('playback'):
                    await self.device.play(pcm)
                check()

        tasks = [asyncio.create_task(produce()),
                 asyncio.create_task(synthesize_segments()),
                 asyncio.create_task(play_segments())]
        try:
            await asyncio.gather(*tasks)
        finally:
            stopping = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return ''.join(reply)

    async def close(self) -> None:
        try:
            await self.cancel()
        finally:
            await asyncio.gather(self.asr.close(), self.llm.close(), self.tts.close())


class VoiceApplication:
    def __init__(self, app: ApplicationContext, config: VoiceConfiguration, registry: ProviderRegistry) -> None:
        self.app, self.config, self.registry = app, config, registry

    async def run(self) -> None:
        from .device import SDKVoiceDevice
        device = SDKVoiceDevice(self.app.robot, self.config.conversation)
        # Factories are constructed only here, on the application's event loop.
        providers: list[ASR | LLM | TTS] = []
        session: VoiceSession | None = None
        turn: asyncio.Task[None] | None = None
        enabled = True
        ready = False
        cleanup_pending = False

        async def cancel_turn() -> None:
            nonlocal turn, cleanup_pending
            assert session is not None
            # Cancel even a scheduled turn before yielding to device cleanup;
            # otherwise it could acquire new resources while stop is pending.
            if turn is not None:
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
                turn = None
            try:
                await session.cancel()
            except Exception:
                cleanup_pending = True
                self.app.logger.warning('设备资源清理未确认，暂停收音；连接可用后重试')
            else:
                cleanup_pending = False

        next_probe = 0.0
        try:
            asr = self.registry.create_asr(self.config.asr)
            providers.append(asr)
            llm = self.registry.create_llm(self.config.llm)
            providers.append(llm)
            tts = self.registry.create_tts(self.config.tts)
            providers.append(tts)
            session = VoiceSession(self.config, asr, llm, tts, device)
            self.app.logger.info('语音应用已启动，等待设备连接')
            while not self.app.shutdown_requested:
                # Poll only the public readiness API; never inspect/reroute business frames in Daemon.
                now = asyncio.get_running_loop().time()
                if now >= next_probe:
                    was_ready = ready
                    ready = await device.ready()
                    next_probe = now + 2
                    if not ready:
                        if turn is not None:
                            await cancel_turn()
                        session.history.clear()
                        if was_ready:
                            self.app.logger.info('设备连接不可用，已停止会话，等待恢复')
                    elif cleanup_pending:
                        # Retry at the readiness probe cadence, even while the
                        # user has paused the microphone. Do not start new work
                        # until all earlier device leases have been released.
                        await cancel_turn()
                try:
                    frame = await self.app.desktop.receive(timeout=0.1)
                except TimeoutError:
                    frame = None
                if isinstance(frame, str):
                    try:
                        message = json.loads(frame)
                    except (ValueError, TypeError):
                        message = None
                    kind = message.get('type') if isinstance(message, dict) else None
                    if kind == 'ctrl.microphone.close':
                        enabled = False
                        await cancel_turn()
                    elif kind == 'ctrl.microphone.open':
                        enabled = True
                if turn is not None and turn.done():
                    try:
                        turn.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        self.app.logger.warning('%s 阶段失败，正在清理；请检查该服务凭据、网络及设备连接', session.last_error_stage)
                        await cancel_turn()
                        await asyncio.sleep(1)
                    turn = None
                if ready and enabled and turn is None and not cleanup_pending:
                    turn = asyncio.create_task(session.turn())
        finally:
            if session is not None:
                try:
                    await cancel_turn()
                finally:
                    await session.close()
            else:
                await asyncio.gather(*(provider.close() for provider in providers))
