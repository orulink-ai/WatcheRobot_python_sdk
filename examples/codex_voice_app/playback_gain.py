"""Packet-independent speech gain and 5 ms linear lookahead peak protection.

This is source PCM processing, not hardware volume. It preserves a constant-
gain waveform instead of applying a nonlinear soft knee to each sample. The
limiter still changes dynamics; acoustic clarity requires listening validation.
"""
from __future__ import annotations

from collections import deque
import math
import struct


class PlaybackGain:
    PROFILE = 'clear-speech-v3'
    TARGET_RMS = 12000
    MAX_GAIN = 16.0
    PEAK_LIMIT = 29000
    NOISE_FLOOR_RMS = 104
    LOOKAHEAD_SECONDS = .005

    def __init__(self, *, sample_rate=24000):
        if type(sample_rate) is not int or sample_rate not in (24000, 48000):
            raise ValueError('Expected 24 or 48 kHz mono PCM')
        self.sample_rate = sample_rate
        self.lookahead = round(sample_rate * self.LOOKAHEAD_SECONDS)
        self.gain = self.limiter_gain = 1.0
        self.energy = None
        self.energy_alpha = -math.expm1(-1 / sample_rate / .04)
        self.rise_alpha = -math.expm1(-1 / sample_rate / .08)
        self.fall_alpha = -math.expm1(-1 / sample_rate / .04)
        self.release_alpha = -math.expm1(-1 / sample_rate / .08)
        self.delay, self.ceilings = deque(), deque()
        self.index = 0
        self.flushed = False
        self.samples = self.input_squares = self.output_squares = 0
        self.input_peak = self.output_peak = self.limited_samples = 0
        self.speech_limited_samples = self.peak_constraint_samples = 0
        self.min_limiter_gain = 1.0
        self.speech_samples = self.speech_input_squares = self.speech_output_squares = 0

    def process(self, pcm: bytes) -> bytes:
        if not isinstance(pcm, bytes) or len(pcm) % 2:
            raise ValueError('Expected mono s16le PCM bytes')
        if self.flushed:
            raise RuntimeError('Playback DSP is finalized')
        output = []
        for (sample,) in struct.iter_unpack('<h', pcm):
            self.energy = (sample * sample if self.energy is None else
                           self.energy + self.energy_alpha * (sample * sample - self.energy))
            rms = math.sqrt(max(0, self.energy))
            # Smoothly approach unity around the noise floor, not a hard bypass.
            activity = min(1.0, max(0.0, (rms - self.NOISE_FLOOR_RMS) / self.NOISE_FLOOR_RMS))
            desired = 1 + activity * (min(self.MAX_GAIN, max(1.0, self.TARGET_RMS / max(1, rms))) - 1)
            alpha = self.rise_alpha if desired > self.gain else self.fall_alpha
            self.gain += alpha * (desired - self.gain)
            value = sample * self.gain
            cap = min(1.0, self.PEAK_LIMIT / abs(value)) if value else 1.0
            self.peak_constraint_samples += cap < 1
            score = cap + self.index / self.lookahead
            while self.ceilings and self.ceilings[-1][1] >= score:
                self.ceilings.pop()
            self.ceilings.append((self.index, score))
            self.delay.append((value, sample))
            emit_index = self.index - self.lookahead
            while self.ceilings and self.ceilings[0][0] < emit_index:
                self.ceilings.popleft()
            if emit_index < 0:
                result = 0
            else:
                value, original = self.delay.popleft()
                ceiling = min(1.0, max(0.0, self.ceilings[0][1] - emit_index / self.lookahead))
                released = self.limiter_gain + self.release_alpha * (1 - self.limiter_gain)
                self.limiter_gain = min(ceiling, released,
                                        self.PEAK_LIMIT / abs(value) if value else 1.0)
                result = round(value * self.limiter_gain)
                # Count meaningful attenuation (>=0.1 dB), not an asymptotic
                # release tail arbitrarily close to unity.
                limited = self.limiter_gain < 10 ** (-.1 / 20)
                self.limited_samples += limited
                self.min_limiter_gain = min(self.min_limiter_gain, self.limiter_gain)
                if abs(original) > self.NOISE_FLOOR_RMS:
                    self.speech_samples += 1
                    self.speech_limited_samples += limited
                    self.speech_input_squares += original * original
                    self.speech_output_squares += result * result
            self.samples += 1
            self.input_squares += sample * sample
            self.output_squares += result * result
            self.input_peak = max(self.input_peak, abs(sample))
            self.output_peak = max(self.output_peak, abs(result))
            self.index += 1
            output.append(result)
        return struct.pack(f'<{len(output)}h', *output)

    def flush(self) -> bytes:
        """Render the delayed tail for offline/natural completion only.

        Emergency stop discards this instance; it must never flush into a
        stopped media lease. Streaming RTC supplies a 300 ms silence tail.
        """
        if self.flushed:
            return b''
        tail = self.process(bytes(self.lookahead * 2)) if self.samples else b''
        self.flushed = True
        return tail

    def diagnostics(self):
        return dict(profile=self.PROFILE, sampleRate=self.sample_rate,
                    gainDb=round(20 * math.log10(self.gain), 2),
                    inputPeak=self.input_peak, outputPeak=self.output_peak,
                    inputRms=round(math.sqrt(self.input_squares / max(1, self.samples)), 1),
                    outputRms=round(math.sqrt(self.output_squares / max(1, self.samples)), 1),
                    samples=self.samples, maxGainDb=round(20 * math.log10(self.MAX_GAIN), 2),
                    peakLimit=self.PEAK_LIMIT, targetRms=self.TARGET_RMS,
                    limitedSamples=self.limited_samples,
                    speechLimitedSamples=self.speech_limited_samples,
                    peakConstraintSamples=self.peak_constraint_samples,
                    minLimiterGainDb=round(20 * math.log10(max(1e-12, self.min_limiter_gain)), 2),
                    limiterGainDb=round(20 * math.log10(max(1e-12, self.limiter_gain)), 2),
                    lookaheadSamples=self.lookahead, lookaheadMs=self.LOOKAHEAD_SECONDS * 1000,
                    speechSamples=self.speech_samples,
                    speechInputRms=round(math.sqrt(self.speech_input_squares / max(1, self.speech_samples)), 1),
                    speechOutputRms=round(math.sqrt(self.speech_output_squares / max(1, self.speech_samples)), 1))
