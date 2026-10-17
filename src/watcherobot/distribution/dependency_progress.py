"""Bounded, cross-platform uv pipe consumption and allowlisted activity events.

uv's plain stderr summaries are observations, not a stable byte-progress API.
Unknown lines remain private; no URLs, paths or package names reach Desktop.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import psutil

from .events import EventSink, ProgressEvent


class _DependencyActivity:
    def __init__(self, events: EventSink) -> None:
        self.events = events
        self.downloaded: set[str] = set()
        self.checkpoint = 0
        self.emit("resolving_dependencies", 0)

    def emit(self, stage: str, checkpoint: int, **counts: int) -> None:
        self.checkpoint = max(self.checkpoint, checkpoint)
        self.events.emit(
            ProgressEvent(
                stage,
                "Preparing Application dependencies",
                {
                    "stage_completed": self.checkpoint,
                    "stage_total": 3,
                    **counts,
                },
            )
        )

    def line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").strip()
        summary = re.fullmatch(
            r"(Resolved|Prepared|Installed|Audited) (\d+) packages? in .+", line
        )
        if summary:
            action, amount = summary.groups()
            count = int(amount)
            if count > 1_000_000:
                return
            if action == "Resolved":
                self.emit("preparing_dependencies", 1)
            else:
                self.emit(
                    "installing_dependency_files",
                    2 if action == "Prepared" else 3,
                    completed_items=count,
                    total_items=count,
                )
            return
        downloading = re.fullmatch(r"Downloading [A-Za-z0-9_.-]+ \([^\r\n]+\)", line)
        downloaded = re.fullmatch(r"Downloaded ([A-Za-z0-9_.-]+)", line)
        if downloading or downloaded:
            if downloaded:
                self.downloaded.add(downloaded.group(1))
            self.emit(
                "downloading_dependencies", 1, completed_items=len(self.downloaded)
            )


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_dependency_install(
    arguments: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    events: EventSink,
    timeout: float = 300,
    max_output_bytes: int = 1024 * 1024,
) -> subprocess.CompletedProcess[bytes]:
    """Drain both pipes while emitting real activity; terminate on any failure."""
    activity = _DependencyActivity(events)
    process = subprocess.Popen(
        list(arguments),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    messages: queue.Queue[tuple[int, bytes | BaseException | None]] = queue.Queue(
        maxsize=64
    )
    stopped = threading.Event()

    def send(item: tuple[int, bytes | BaseException | None]) -> None:
        while not stopped.is_set():
            try:
                messages.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def read(index: int, stream: BinaryIO) -> None:
        try:
            while chunk := os.read(stream.fileno(), 4096):
                send((index, chunk))
        except OSError as exc:
            send((index, exc))
        finally:
            send((index, None))

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=read, args=(index, pipe), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    for thread in threads:
        thread.start()
    output = [bytearray(), bytearray()]
    pending = b""
    ended: set[int] = set()
    deadline = time.monotonic() + timeout
    try:
        while len(ended) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            try:
                index, chunk = messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if isinstance(chunk, BaseException):
                raise chunk
            if chunk is None:
                ended.add(index)
                continue
            if len(output[index]) + len(chunk) > max_output_bytes:
                raise ValueError("Application environment output exceeds limit")
            output[index].extend(chunk)
            if index == 1:
                lines = re.split(rb"[\r\n]", pending + chunk)
                pending = lines.pop()
                for line in lines:
                    activity.line(line)
        if pending:
            activity.line(pending)
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(
            arguments, code, bytes(output[0]), bytes(output[1])
        )
    finally:
        stopped.set()
        if process.poll() is None:
            _kill_tree(process)
        process.wait()
        for thread in threads:
            thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
