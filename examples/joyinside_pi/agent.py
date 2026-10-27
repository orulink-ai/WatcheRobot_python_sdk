"""A local Pi RPC adapter; no shell expansion or cloud callback server."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Awaitable, Callable, Iterator
import uuid


def pi_command() -> list[str]:
    override = os.environ.get("PI_COMMAND_JSON")
    if override:
        command = json.loads(override)
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
            raise ValueError("PI_COMMAND_JSON must be a nonempty JSON argv array")
        if Path(command[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("Use a native executable (e.g. node plus Pi CLI), not a shell script")
        return command
    launcher = shutil.which("pi.cmd" if os.name == "nt" else "pi")
    if not launcher:
        raise RuntimeError("Install Pi and add it to PATH, or configure PI_COMMAND_JSON")
    if os.name != "nt":
        return [launcher, "--mode", "rpc", "--no-session"]
    node = shutil.which("node")
    for package in ("@earendil-works/pi-coding-agent", "@mariozechner/pi-coding-agent"):
        root = Path(launcher).parent / "node_modules" / package
        manifest = root / "package.json"
        if manifest.is_file() and node:
            cli = root / json.loads(manifest.read_text(encoding="utf-8"))["bin"]["pi"]
            if cli.is_file():
                return [node, str(cli), "--mode", "rpc", "--no-session"]
    raise RuntimeError("Cannot resolve Pi's Node entry point; configure PI_COMMAND_JSON")


def speech_chunks(text: str, limit: int = 900) -> Iterator[str]:
    if not 3 <= limit < 1000:
        raise ValueError("chunk limit must be 3..999")
    chunk, size = "", 0
    for char in text:
        width = len(char.encode("utf-8"))
        if width > limit:
            raise ValueError("chunk limit too small for character")
        if size + width > limit:
            yield chunk
            chunk, size = "", 0
        chunk += char
        size += width
    if chunk:
        yield chunk


class PiAgent:
    def __init__(self, command: list[str] | None = None, *, cwd: str | None = None) -> None:
        self.command = command if command is not None else pi_command()
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> PiAgent:
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024, creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return self

    async def _send(self, data: dict) -> None:
        if self.process is None or self.process.returncode is not None or self.process.stdin is None:
            raise RuntimeError("Pi process is closed")
        self.process.stdin.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def ask(self, text: str, *, timeout: float = 120,
                  on_text: Callable[[str], Awaitable[None]] | None = None) -> str:
        """Return the final answer; optionally await visible text deltas.

        The callback receives assistant prose, including pre-tool messages.
        Its await provides backpressure. Thinking and tool arguments are never
        forwarded. Streamed speech cannot be retracted after a provider retry.
        """
        async with self._lock:
            try:
                return await asyncio.wait_for(self._ask(text, on_text), timeout)
            except asyncio.TimeoutError as error:
                await self.close()
                raise TimeoutError("Pi reply timed out") from error
            except BaseException:
                # Retire this process so late session events cannot complete
                # a later prompt. The next Application run creates a new Pi.
                await self.close()
                raise

    async def _ask(self, text: str, on_text: Callable[[str], Awaitable[None]] | None) -> str:
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
