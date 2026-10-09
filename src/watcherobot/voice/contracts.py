"""Public provider contracts. Audio entering ASR is 16 kHz mono S16LE."""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator, Protocol, Sequence


class VoiceError(RuntimeError):
    """Safe public error; never include provider response bodies or credentials."""


@dataclass(frozen=True)
class Transcript:
    text: str
    final: bool = True


@dataclass(frozen=True)
class AudioChunk:
    data: bytes
    sample_rate: int = 24000
    channels: int = 1
    encoding: str = "pcm_s16le"


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class CatalogItem:
    id: str
    name: str


@dataclass(frozen=True)
class Catalog:
    items: tuple[CatalogItem, ...] = ()
    supported: bool = False
    scope: str = "unsupported"
    note: str = "当前适配器不提供此项查询；请手动配置 ID。"


class ASR(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncGenerator[Transcript, None]: ...
    async def close(self) -> None: ...


class LLM(Protocol):
    def generate(self, messages: Sequence[Message]) -> AsyncGenerator[str, None]: ...
    async def close(self) -> None: ...


class TTS(Protocol):
    def synthesize(self, text: str) -> AsyncGenerator[AudioChunk, None]: ...
    async def close(self) -> None: ...
