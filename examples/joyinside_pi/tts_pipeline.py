"""Bounded PCM preparation, independent of the cloud and device transports."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing
import logging
import time

from watcherobot.audio import MAX_AUDIO_BYTES


class _PreparedPCM:
    def __init__(self, segment_limit: int, byte_budget: int, slots: int) -> None:
        if segment_limit < 2 or byte_budget < segment_limit or slots < 1:
            raise ValueError('Invalid PCM preparation budget')
        self.segment_limit, self.byte_budget, self.slots = segment_limit, byte_budget, slots
        self.bytes = self.count = 0
        self.ready: deque[tuple[int, bytes, float]] = deque()
        self.ended = self.stopping = False
        self.changed = asyncio.Condition()

    async def reserve(self) -> None:
        async with self.changed:
            await self.changed.wait_for(lambda: self.stopping or (
                self.count < self.slots and self.bytes + self.segment_limit <= self.byte_budget))
            if self.stopping:
                raise asyncio.CancelledError
            self.bytes += self.segment_limit
            self.count += 1

    async def put(self, number: int, pcm: bytes) -> None:
        async with self.changed:
            if self.stopping:
                raise asyncio.CancelledError
            self.bytes -= self.segment_limit - len(pcm)
            self.ready.append((number, pcm, time.monotonic()))
            self.changed.notify_all()

    async def take(self) -> tuple[int, bytes, float] | None:
        async with self.changed:
            await self.changed.wait_for(lambda: self.stopping or self.ready or self.ended)
            if self.stopping:
                raise asyncio.CancelledError
            return self.ready.popleft() if self.ready else None

    async def release(self, pcm: bytes) -> None:
        async with self.changed:
            self.bytes -= len(pcm)
            self.count -= 1
            self.changed.notify_all()


async def _text_segments(segments: Iterable[str] | AsyncIterable[str]) -> AsyncIterator[str]:
    """Own the async iterator, including when it is suspended after yielding."""
    if isinstance(segments, AsyncIterable):
        iterator = aiter(segments)
        try:
            async for text in iterator:
                yield text
        finally:
            close = getattr(iterator, 'aclose', None)
            if close is not None:
                await close()
    else:
        for text in segments:
            yield text


async def run_tts_pipeline(
    segments: Iterable[str] | AsyncIterable[str], *,
    synthesize: Callable[[str, int], Awaitable[bytes]],
    play_segment: Callable[[bytes, int], Awaitable[None]],
    play_stream: Callable | None = None,
    before_play: Callable[[], Awaitable[None]] | None = None,
    max_segment_bytes: int = MAX_AUDIO_BYTES,
    buffer_bytes: int = 2 * MAX_AUDIO_BYTES,
    max_segments: int = 8,
    logger: logging.Logger | None = None,
) -> None:
    """Prepare continuously within byte/slot budgets; never overlap device streams.

    The reservation includes synthesis in progress and PCM held by the consumer.
    Continuous devices consume one iterator for the entire reply. A v1 device
    starts its first segment immediately, then coalesces already prepared PCM.
    An optional playback gate waits after the first PCM is ready, without
    blocking bounded synthesis or opening a device stream prematurely.
    """
    queue = _PreparedPCM(max_segment_bytes, buffer_bytes, max_segments)

    async def produce() -> None:
        async with aclosing(_text_segments(segments)) as texts:
            number = 0
            async for text in texts:
                number += 1
                if not isinstance(text, str):
                    raise ValueError('TTS 播报分段必须是文本')
                if not text.strip():
                    continue
                await queue.reserve()
                pcm = await synthesize(text, number)
                if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > max_segment_bytes:
                    raise ValueError('TTS 分段 PCM 无效或超过预算')
                await queue.put(number, pcm)
                if logger:
                    logger.info('speech_queue stage=ready segment=%d buffered_segments=%d buffered_bytes=%d',
                                number, queue.count, queue.bytes)
        async with queue.changed:
            queue.ended = True
            queue.changed.notify_all()

    async def frames(first):
        item = first
        while item is not None:
            number, pcm, ready = item
            if logger:
                logger.info('speech_queue stage=consume segment=%d pcm_ms=%d queue_wait_ms=%d',
                            number, round(len(pcm) / 48), round((time.monotonic() - ready) * 1000))
            try:
                yield pcm
            finally:
                await queue.release(pcm)
            item = await queue.take()

    async def consume() -> None:
        first = await queue.take()
        if first is None:
            return
        if before_play is not None:
            await before_play()
        if queue.stopping:
            # A user gate may suppress CancelledError during its own cleanup.
            raise asyncio.CancelledError
        if play_stream is not None:
            async with aclosing(frames(first)) as audio:
                await play_stream(audio)
            return
        item, initial = first, True
        while item is not None:
            batch = [item]
            size = len(item[1])
            if not initial:
                # No added waiting: only combine PCM already prepared now.
                async with queue.changed:
                    while queue.ready and size + len(queue.ready[0][1]) <= max_segment_bytes:
                        following = queue.ready.popleft()
                        batch.append(following)
                        size += len(following[1])
            try:
                await play_segment(b''.join(part[1] for part in batch), item[0])
            finally:
                for part in batch:
                    await queue.release(part[1])
            initial = False
            item = await queue.take()

    tasks = [asyncio.create_task(produce()), asyncio.create_task(consume())]
    completion = asyncio.gather(*tasks)
    try:
        # Mark stopping before forwarding cancellation to resistant callbacks.
        await asyncio.shield(completion)
    finally:
        queue.stopping = True
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(completion, return_exceptions=True)
        queue.ready.clear()
