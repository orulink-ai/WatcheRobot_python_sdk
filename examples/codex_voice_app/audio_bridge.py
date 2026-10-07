"""Validate and assemble bounded Codex PCM utterances for SDK playback."""
from __future__ import annotations

import base64
import binascii

import av


class OutputBuffer:
    def __init__(self, max_seconds: float = 30) -> None:
        self.max_seconds = max_seconds
        self.data = bytearray()
        self.format: tuple[int, int] | None = None

    @property
    def pending(self) -> bool:
        return bool(self.data)

    def add(self, audio: dict) -> None:
        rate, channels = audio.get('sampleRate'), audio.get('numChannels')
        if (type(rate) is not int or not 8000 <= rate <= 48000
                or type(channels) is not int or channels not in (1, 2)):
            raise ValueError('Unsupported Codex PCM audio format')
        try:
            pcm = base64.b64decode(audio['data'], validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error) as error:
            raise ValueError('Invalid Codex PCM base64') from error
        if not pcm or len(pcm) % (2 * channels):
            raise ValueError('Incomplete PCM sample')
        samples = audio.get('samplesPerChannel')
        if samples is not None and (type(samples) is not int or samples != len(pcm) // (2 * channels)):
            raise ValueError('PCM sample count mismatch')
        if self.format is not None and self.format != (rate, channels):
            raise ValueError('PCM format changed mid utterance')
        if len(self.data) + len(pcm) > rate * channels * 2 * self.max_seconds:
            raise ValueError('Codex output audio exceeds utterance limit')
        self.format = (rate, channels)
        self.data.extend(pcm)

    def take(self, *, sample_rate=24000) -> bytes:
        if type(sample_rate) is not int or sample_rate not in (24000, 48000):
            raise ValueError('Unsupported output sample rate')
        if not self.data or self.format is None:
            return b''
        pcm, (rate, channels) = bytes(self.data), self.format
        self.data.clear()
        self.format = None
        if (rate, channels) == (sample_rate, 1):
            return pcm
        frame = av.AudioFrame(format='s16', layout='mono' if channels == 1 else 'stereo',
                              samples=len(pcm) // (2 * channels))
        frame.sample_rate = rate
        frame.planes[0].update(pcm)
        resampler = av.AudioResampler(format='s16', layout='mono', rate=sample_rate)
        frames = [*resampler.resample(frame), *resampler.resample(None)]
        return b''.join(bytes(output.planes[0])[:output.samples * 2] for output in frames)
