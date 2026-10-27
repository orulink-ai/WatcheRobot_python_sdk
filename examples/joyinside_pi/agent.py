"""A local Pi RPC adapter; no shell expansion or cloud callback server."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
from typing import Awaitable, Callable, Iterator
import uuid


def pi_command() -> list[str]:
    extension = ["--extension", str(Path(__file__).with_name("voice_extension.mjs"))]
    override = os.environ.get("PI_COMMAND_JSON")
    if override:
        command = json.loads(override)
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
            raise ValueError("PI_COMMAND_JSON must be a nonempty JSON argv array")
        if Path(command[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("Use a native executable (e.g. node plus Pi CLI), not a shell script")
        return command + extension
    launcher = shutil.which("pi.cmd" if os.name == "nt" else "pi")
    if not launcher:
        raise RuntimeError("Install Pi and add it to PATH, or configure PI_COMMAND_JSON")
    if os.name != "nt":
        return [launcher, "--mode", "rpc", "--no-session", *extension]
    node = shutil.which("node")
    for package in ("@earendil-works/pi-coding-agent", "@mariozechner/pi-coding-agent"):
        root = Path(launcher).parent / "node_modules" / package
        manifest = root / "package.json"
        if manifest.is_file() and node:
            cli = root / json.loads(manifest.read_text(encoding="utf-8"))["bin"]["pi"]
            if cli.is_file():
                return [node, str(cli), "--mode", "rpc", "--no-session", *extension]
    raise RuntimeError("Cannot resolve Pi's Node entry point; configure PI_COMMAND_JSON")


def speech_chunks(text: str, limit: int = 900) -> Iterator[str]:
    """Start with at most 28 characters, then use natural segments up to 80.

    Short URLs, numbers and ASCII identifiers stay intact where the budgets
    permit. Very long tokens still obey the hard character and UTF-8 limits.
    """
    if not 3 <= limit < 1000:
        raise ValueError("chunk limit must be 3..999")
    widths = [len(char.encode("utf-8")) for char in text]
    if any(width > limit for width in widths):
        raise ValueError("chunk limit too small for character")
    token_pattern = r"https?://[^\s，。！？、；：‘’“”\"<>]+|[A-Za-z0-9_]+(?:[.,:/@+-][A-Za-z0-9_]+)*"
    tokens = [(match.start(), match.start() + len(match[0].rstrip(".,!?;:")))
              for match in re.finditer(token_pattern, text)]
    protected = {boundary for begin, end in tokens for boundary in range(begin + 1, end)}
    tails = "。！？!?.,，、;；:：’”\"')）】》」』"
    start, first = 0, True
    while start < len(text):
        size, maximum = 0, start
        budget = 28 if first else 80
        while maximum < min(len(text), start + budget):
            if size + widths[maximum] > limit:
                break
            size += widths[maximum]
            maximum += 1
        end = None
        for boundary in range(start + 1, maximum + 1):
            char = text[boundary - 1]
            sentence_end = char in "。！？!?\n" or (
                char == "." and (boundary == len(text) or text[boundary].isspace()))
            if sentence_end and boundary not in protected:
                end = boundary
                # Consume repeated punctuation and closing quotes together.
                while end < maximum and text[end] in tails:
                    end += 1
                if end == len(text) or text[end] not in tails:
                    break
                end = None
        if end is None:
            end = maximum
            if maximum < len(text):
                pauses = [boundary for boundary in range(start + 1, maximum + 1)
                          if text[boundary - 1] in ",，、;；:："
                          and boundary not in protected
                          and sum(char.isalnum() for char in text[start:boundary]) >= 12]
                if pauses:
                    end = pauses[-1]
                elif maximum in protected:
                    begin = next(begin for begin, finish in tokens if begin < maximum < finish)
                    if begin > start and any(char.isalnum() for char in text[start:begin]):
                        end = begin
                # Reserve a preceding character for a punctuation tail that
                # would otherwise become a segment on its own.
                while end > start + 1 and end < len(text) and text[end] in tails:
                    end -= 1
        yield text[start:end]
        start, first = end, False


class PiAgent:
    def __init__(self, command: list[str] | None = None, *, cwd: str | None = None) -> None:
        self.command = command if command is not None else pi_command()
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._request_task: asyncio.Task | None = None

    async def __aenter__(self) -> PiAgent:
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024, **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        return self

    async def _send(self, data: dict) -> None:
        if self.process is None or self.process.returncode is not None or self.process.stdin is None:
            raise RuntimeError("Pi process is closed")
        self.process.stdin.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def ask(self, text: str, *, timeout: float = 120,
                  on_speak: Callable[[str], Awaitable[None]] | None = None,
                  on_event: Callable[[dict], Awaitable[None]] | None = None,
                  on_text: Callable[[str], Awaitable[None]] | None = None) -> str:
        async with self._lock:
            task = asyncio.create_task(self._ask(text, on_speak=on_speak, on_event=on_event, on_text=on_text))
            self._request_task = task
            try:
                return await asyncio.wait_for(task, timeout)
            except BaseException:
                # Retire this process so late session events cannot complete
                # a later prompt. The next Application run creates a new Pi.
                await self.close()
                raise
            finally:
                if self._request_task is task:
                    self._request_task = None

    async def _ask(self, text: str, *, on_speak: Callable[[str], Awaitable[None]] | None = None,
                   on_event: Callable[[dict], Awaitable[None]] | None = None,
                   on_text: Callable[[str], Awaitable[None]] | None = None) -> str:
        request = uuid.uuid4().hex
        await self._send({"id": request, "type": "prompt", "message": text})
        assert self.process is not None and self.process.stdout is not None
        reply, failed, accepted, settled = "", False, False, False
        blocks: dict[int, str] = {}
        streamed = ""
        previous_message = False
        total = 0

        async def publish(visible: str) -> None:
            nonlocal streamed, total
            if on_text is None:
                return
            if not visible.startswith(streamed):
                raise RuntimeError("Pi changed already streamed text; please retry the turn")
            delta = visible[len(streamed):]
            if delta:
                total += len(delta)
                if total > 16000:
                    raise RuntimeError("Pi voice reply exceeds 16000 characters")
                if not streamed and previous_message:
                    await on_text("\n")
                await on_text(delta)
                streamed = visible

        while True:
            line = await self.process.stdout.readline()
            if not line:
                raise RuntimeError("Pi exited before completing the prompt")
            record = json.loads(line)
            if not isinstance(record, dict):
                raise RuntimeError("Invalid Pi RPC record")
            kind = record.get("type")
            # Observers may enqueue bounded speculative text, but must never
            # wait for cloud synthesis or playback: RPC must keep draining.
            if on_event is not None:
                await on_event(record)
            if kind == "response" and record.get("id") == request:
                if record.get("success") is not True:
                    raise RuntimeError("Pi rejected the prompt")
                disposition = record.get("data", {}).get("disposition")
                if disposition == "handled":
                    raise RuntimeError("Pi handled the command without starting a reply")
                accepted = True
            elif kind == "message_start" and record.get("message", {}).get("role") == "assistant":
                blocks, streamed = {}, ""
            elif kind == "message_update" and on_text is not None:
                update = record.get("assistantMessageEvent", {})
                update_kind = update.get("type")
                if update_kind not in {"text_delta", "text_end"}:
                    continue
                index = update.get("contentIndex")
                if not isinstance(index, int) or not 0 <= index < 128:
                    raise RuntimeError("Invalid Pi text block index")
                value = update.get("delta" if update_kind == "text_delta" else "content")
                if not isinstance(value, str):
                    raise RuntimeError("Invalid Pi text update")
                blocks[index] = blocks.get(index, "") + value if update_kind == "text_delta" else value
                await publish("".join(blocks[i] for i in sorted(blocks)))
            elif kind == "message_end":
                message = record.get("message", {})
                if message.get("role") == "assistant":
                    failed = message.get("stopReason") in {"error", "aborted"}
                    reply = "".join(block["text"] for block in message.get("content", [])
                                    if block.get("type") == "text" and isinstance(block.get("text"), str))
                    if on_text is not None:
                        if failed:
                            raise RuntimeError("Pi generation failed after partial output")
                        await publish(reply)
                        previous_message = previous_message or bool(streamed)
                        blocks, streamed = {}, ""
            elif kind == "agent_settled":
                settled = True
                failed = failed or bool(record.get("aborted"))
            elif kind == "extension_ui_request":
                if record.get("method") == "confirm" and record.get("title") == "watcherobot.voice_reply.v1":
                    spoken = record.get("message")
                    identity = record.get("id")
                    if (on_speak is None or not isinstance(identity, str) or not identity
                            or not isinstance(spoken, str) or not spoken.strip()
                            or len(spoken) > 80 or len(spoken.encode("utf-8")) > 900):
                        raise RuntimeError("Pi voice reply has no valid playback request or owner")
                    # The tool stays blocked in Pi until physical playback finishes.
                    await on_speak(spoken)
                    await self._send({"type": "extension_ui_response", "id": identity, "confirmed": True})
                    continue
                if record.get("method") in {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text"}:
                    continue
                # This voice example has no extension UI. An interactive
                # request must fail explicitly rather than hang indefinitely.
                raise RuntimeError("Pi extension requires interactive UI")
            if settled and accepted:
                if failed or not reply.strip():
                    raise RuntimeError("Pi finished without a successful text reply")
                return reply

    async def close(self) -> None:
        async with self._close_lock:
            await self._close()

    async def _close(self) -> None:
        request = self._request_task
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        process = self.process
        if process is None or process.returncode is not None:
            return
        try:
            await self._send({"id": uuid.uuid4().hex, "type": "abort"})
        except (BrokenPipeError, ConnectionError, RuntimeError):
            pass
        if process.stdin:
            process.stdin.close()
        # Keep reading during orderly shutdown to relieve stdout backpressure.
        async def drain() -> None:
            if process.stdout:
                while await process.stdout.read(65536):
                    pass
        draining = asyncio.create_task(drain())
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        finally:
            await draining

    async def __aexit__(self, *args: object) -> None:
        await self.close()
