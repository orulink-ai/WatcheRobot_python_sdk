"""``reminder.speech`` 的纯逻辑测试：缓存定位与降级选择（合成器注入假实现）。"""

import asyncio
from pathlib import Path

from reminder.speech import ReminderSpeech


async def _synthesize_ok(text: str, out_wav: Path) -> bool:
    # 与真实 synthesize_text_to_wav 的契约一致：负责创建父目录并写出 WAV。
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(b"RIFF-synthetic")
    return True


async def _synthesize_fail(text: str, out_wav: Path) -> bool:
    return False


def _run(coro) -> object:
    return asyncio.run(coro)


class TestCachePath:
    def test_stable_per_text(self) -> None:
        speech = ReminderSpeech(cache_dir=Path("cache"), fallback_wav=Path("fb.wav"))
        assert speech.cache_path_for("晚上好") == speech.cache_path_for("晚上好")
        assert speech.cache_path_for("晚上好") != speech.cache_path_for("早上好")
        assert speech.cache_path_for("晚上好").suffix == ".wav"


class TestWavFor:
    def test_returns_cached_file_when_present(self, tmp_path: Path) -> None:
        speech = ReminderSpeech(cache_dir=tmp_path / "cache", fallback_wav=tmp_path / "fb.wav")
        cached = speech.cache_path_for("晚上好")
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"cached")

        result = _run(speech.wav_for("晚上好"))

        assert result == cached
        assert result.read_bytes() == b"cached"

    def test_synthesizes_when_not_cached(self, tmp_path: Path) -> None:
        speech = ReminderSpeech(
            cache_dir=tmp_path / "cache",
            fallback_wav=tmp_path / "fb.wav",
            synthesizer=_synthesize_ok,
        )

        result = _run(speech.wav_for("晚上好"))

        assert result.read_bytes() == b"RIFF-synthetic"

    def test_falls_back_when_synthesis_fails(self, tmp_path: Path) -> None:
        fallback = tmp_path / "fb.wav"
        fallback.write_bytes(b"fallback")
        speech = ReminderSpeech(
            cache_dir=tmp_path / "cache",
            fallback_wav=fallback,
            synthesizer=_synthesize_fail,
        )

        result = _run(speech.wav_for("晚上好"))

        assert result == fallback
        assert result.read_bytes() == b"fallback"

    def test_synthesis_cached_after_success(self, tmp_path: Path) -> None:
        speech = ReminderSpeech(
            cache_dir=tmp_path / "cache",
            fallback_wav=tmp_path / "fb.wav",
            synthesizer=_synthesize_ok,
        )

        first = _run(speech.wav_for("晚上好"))
        second = _run(speech.wav_for("晚上好"))

        assert first == second
        assert second.read_bytes() == b"RIFF-synthetic"