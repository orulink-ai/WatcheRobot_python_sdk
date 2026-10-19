"""Bounded speculative speech, committed only against Pi's settled final text.

RPC delivery never waits for synthesis or queue space. A candidate owns its
pipeline until it is discarded or explicitly committed by the conversation.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import logging
import time

from agent import speech_chunks


class _TextSource:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.pending: deque[str] = deque()
        self.ended = False
        self.changed = asyncio.Event()

    def put(self, text: str) -> bool:
        if self.ended or len(self.pending) >= self.limit:
            return False
        self.pending.append(text)
        self.changed.set()
        return True

    def finish(self) -> None:
        self.ended = True
        self.changed.set()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        while not self.pending:
            if self.ended:
                raise StopAsyncIteration
            self.changed.clear()
            await self.changed.wait()
        return self.pending.popleft()

    async def aclose(self) -> None:
        self.pending.clear()
        self.finish()


class TextSpeechPreview:
    """Prepare short sentences early; no audio may reach a device before play()."""

    def __init__(self, say_segments: Callable, *, max_pending: int = 32,
                 max_text: int = 16384, logger: logging.Logger | None = None) -> None:
        if max_pending < 1 or max_text < 1:
            raise ValueError('Invalid text preview budget')
        self.say_segments = say_segments
        self.max_pending, self.max_text = max_pending, max_text
        self.blocks: dict[int, str] = {}
        self.text = self.sent = ''
        self.sent_parts: list[str] = []
        self.final = ''
        self.blocked = self.closed = False
        self.source: _TextSource | None = None
        self.task: asyncio.Task | None = None
        self.retired: set[asyncio.Task] = set()
        self.gate = asyncio.Event()
        self.logger = logger
        self.started = time.monotonic()

    def _log(self, stage: str) -> None:
        if self.logger is not None:
            self.logger.info('speech_preview stage=%s segments=%d elapsed_ms=%d',
                             stage, len(self.sent_parts), round((time.monotonic() - self.started) * 1000))

    async def _discard(self) -> None:
        task, source = self.task, self.source
        self.task = self.source = None
        if task is not None:
            self._log('discard')
            if not task.done():
                task.cancel()
            self.retired.add(task)

            def finished(done: asyncio.Task) -> None:
                self.retired.discard(done)
                if not done.cancelled():
                    done.exception()  # Retrieve discarded pre-commit errors.
            task.add_done_callback(finished)
        if source is not None:
            await source.aclose()
        self.text = self.sent = self.final = ''
        self.sent_parts.clear()
        self.gate = asyncio.Event()

    async def join(self) -> None:
        """Wait at an ownership boundary, never in the RPC event observer."""
        if self.retired:
            await asyncio.gather(*self.retired, return_exceptions=True)

    async def _reset(self, *, blocked: bool = False) -> None:
        await self._discard()
        self.blocks.clear()
        self.blocked = blocked

    async def _update(self, text: str, *, final: bool = False) -> None:
        if len(text) > self.max_text:
            await self._reset(blocked=True)
            return
        parts = list(speech_chunks(text))
        # Hold one segment of lookahead: punctuation, closing quotes and short
        # identifiers can continue in the next delta. Final flush has no hold.
        ready = parts if final else parts[:-1]
        if ready[:len(self.sent_parts)] != self.sent_parts:
            await self._discard()
        self.text = text
        if final:
            self.final = text
        offset = 0
        for part in ready:
            offset += len(part)
            if offset <= len(self.sent):
                continue
            if self.source is None:
                if any(not task.done() for task in self.retired):
                    # A previous synthesis still owns the session. Do not
                    # create more tasks or stall the RPC reader waiting for it.
                    return
                self.source = _TextSource(self.max_pending)
                self.task = asyncio.create_task(self.say_segments(self.source, before_play=self.gate.wait))
            if not self.source.put(part):
                # Saturation discards precomputation, not RPC events. The final
                # reply will use the normal bounded pipeline after settlement.
                await self._reset(blocked=True)
                return
            self.sent += part
            self.sent_parts.append(part)
            self._log('prepare')
        if final:
            self.final = text
            if self.source is not None:
                self.source.finish()

    async def handle(self, record: dict) -> None:
        if self.closed:
            return
        kind = record.get('type')
        if kind in {'auto_retry_start', 'auto_compaction_start', 'tool_execution_start'}:
            await self._reset(blocked=True)
        elif kind == 'extension_ui_request' and record.get('method') == 'confirm':
            # Release the speculative session owner before the preface speaks.
            await self._reset(blocked=True)
        elif kind == 'message_start':
            message = record.get('message', {})
            await self._reset(blocked=message.get('role') != 'assistant')
        elif kind == 'message_update' and not self.blocked:
            event = record.get('assistantMessageEvent', {})
            event_type = event.get('type')
            if event_type in {'toolcall_start', 'toolcall_end', 'error'}:
                await self._reset(blocked=True)
                return
            index = event.get('contentIndex')
            if type(index) is not int or not 0 <= index < 128:
                return
            if event_type == 'text_delta' and isinstance(event.get('delta'), str):
                self.blocks[index] = self.blocks.get(index, '') + event['delta']
            elif event_type == 'text_end' and isinstance(event.get('content'), str):
                self.blocks[index] = event['content']
            else:
                return  # Thinking and serialized tool arguments are not speech.
            await self._update(''.join(self.blocks[i] for i in sorted(self.blocks)))
        elif kind == 'message_end':
            message = record.get('message', {})
            if message.get('role') != 'assistant':
                return
            content = message.get('content', [])
            if (message.get('stopReason') not in {None, 'stop'}
                    or any(block.get('type') == 'toolCall' for block in content)):
                await self._reset(blocked=True)
                return
            if not self.blocked:
                final = ''.join(block['text'] for block in content
                                if block.get('type') == 'text' and isinstance(block.get('text'), str))
                await self._update(final, final=True)

    async def play(self, final: str) -> bool:
        """Only call after ask() returns a validated, successful settled reply.

        A pre-commit synthesis failure is recoverable through normal TTS.
        After opening the gate, failures propagate; replaying would duplicate
        speech that may already have reached the robot.
        """
        if self.closed or self.blocked or self.final != final:
            self._log('fallback')
            await self._discard()
            await self.join()
            return False
        if self.task is None:
            await self.join()
            await self._update(final, final=True)
            if self.task is None:
                return False
        if self.task.done():
            self._log('fallback')
            await self._discard()
            await self.join()
            return False
        self._log('commit')
        self.gate.set()
        await self.task
        return True

    async def close(self) -> None:
        self.closed = True
        await self._discard()
        await self.join()
