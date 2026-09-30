"""Explicit factories: Applications can register or replace any provider."""
from __future__ import annotations

from typing import Callable

from ..configuration import ConfigurationError, ModelConfig
from ..contracts import ASR, LLM, TTS
from .http import ChatLLM, CloudTTS
from .asr import StreamingASR


class ProviderRegistry:
    def __init__(self) -> None:
        self._asr: dict[str, Callable[[ModelConfig], ASR]] = {}
        self._llm: dict[str, Callable[[ModelConfig], LLM]] = {}
        self._tts: dict[str, Callable[[ModelConfig], TTS]] = {}

    def register_asr(self, name: str, factory: Callable[[ModelConfig], ASR]) -> None:
        self._asr[name] = factory

    def register_llm(self, name: str, factory: Callable[[ModelConfig], LLM]) -> None:
        self._llm[name] = factory

    def register_tts(self, name: str, factory: Callable[[ModelConfig], TTS]) -> None:
        self._tts[name] = factory

    def create_asr(self, config: ModelConfig) -> ASR:
        if config.provider not in self._asr:
            raise ConfigurationError(f'ASR 未注册供应商：{config.provider}')
        return self._asr[config.provider](config)

    def create_llm(self, config: ModelConfig) -> LLM:
        if config.provider not in self._llm:
            raise ConfigurationError(f'LLM 未注册供应商：{config.provider}')
        return self._llm[config.provider](config)

    def create_tts(self, config: ModelConfig) -> TTS:
        if config.provider not in self._tts:
            raise ConfigurationError(f'TTS 未注册供应商：{config.provider}')
        return self._tts[config.provider](config)

    @classmethod
    def builtin(cls) -> ProviderRegistry:
        registry = cls()
        for name in ('qwen', 'ark', 'openai_chat'):
            registry.register_llm(name, ChatLLM)
        for name in ('volcengine', 'deepgram', 'cartesia', 'elevenlabs', 'minimaxi'):
            registry.register_tts(name, CloudTTS)
        for name in ('volcengine', 'aliyun', 'deepgram'):
            registry.register_asr(name, StreamingASR)
        return registry
