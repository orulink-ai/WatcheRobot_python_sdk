"""Incremental, bounded speech segments independent of Pi and device I/O."""
from __future__ import annotations


class SentenceBuffer:
    def __init__(self, *, min_chars: int = 12, max_chars: int = 96) -> None:
        if not 1 <= min_chars <= max_chars <= 200:
            raise ValueError("require 1 <= min_chars <= max_chars <= 200")
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.pending = ""

    def feed(self, text: str) -> list[str]:
        self.pending += text
        chunks = []
        while self.pending:
            end = 0
            for i, char in enumerate(self.pending[:self.max_chars]):
                size = i + 1
                # An ASCII dot needs following whitespace. It can otherwise
                # be an incomplete decimal, version number, or hostname.
                full_stop = char == "." and size < len(self.pending) and self.pending[size].isspace()
                if (char in "。！？!?；;\n" or full_stop
                        or (char in "，,：:" and size >= self.min_chars)
                        or size == self.max_chars):
                    end = size
                    break
            if not end:
                break
            chunk, self.pending = self.pending[:end], self.pending[end:]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    def flush(self, *, idle: bool = False) -> list[str]:
        if idle and len(self.pending.strip()) < self.min_chars:
            return []
        chunk, self.pending = self.pending, ""
        return [chunk] if chunk.strip() else []
