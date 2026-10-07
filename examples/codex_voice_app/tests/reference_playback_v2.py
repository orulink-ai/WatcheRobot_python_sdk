"""Maximum speech loudness compensation, before device Opus encoding.

This changes source PCM, not hardware/system volume. The device AEC still
references the final samples written to its speaker. No added audio buffering.
"""
from __future__ import annotations

import math
import struct


class PlaybackGain:
    PROFILE = 'maximum-speech-v2'
    SAMPLE_RATE = 24000
    TARGET_RMS = 12000
    MAX_GAIN = 16.0
    PEAK_LIMIT = 29000
    SOFT_KNEE = 22000
    NOISE_FLOOR_RMS = 104
    MAX_BLOCK_SAMPLES = 480  # Same control cadence for arbitrarily large input.
    GAIN_RISE_SECONDS = .08

    def __init__(self):
        self.gain = 1.0
        self.samples = self.input_squares = self.output_squares = 0
        self.input_peak = self.output_peak = 0
        self.compressed_samples = 0
        self.speech_samples = self.speech_input_squares = self.speech_output_squares = 0

    def _limit(self, value: float) -> int:
        """Unity slope at the knee; asymptotically approach the safe ceiling.

        A transient is compressed locally instead of reducing every sample
        in its frame. This deliberately changes crest factor, not hardware
        gain; it is not a promise of acoustically distortion-free playback.
        """
        magnitude = abs(value)
        if magnitude > self.SOFT_KNEE:
            self.compressed_samples += 1
            headroom = self.PEAK_LIMIT - self.SOFT_KNEE
            magnitude = self.SOFT_KNEE + headroom * -math.expm1(
                -(magnitude - self.SOFT_KNEE) / headroom)
        return round(math.copysign(magnitude, value))

    def process(self, pcm: bytes) -> bytes:
        if not isinstance(pcm, bytes) or len(pcm) % 2:
            raise ValueError('Expected mono s16le PCM bytes')
        if not pcm:
            return pcm
        samples = [item[0] for item in struct.iter_unpack('<h', pcm)]
        output = []
        squares = 0
        for offset in range(0, len(samples), self.MAX_BLOCK_SAMPLES):
            block = samples[offset:offset + self.MAX_BLOCK_SAMPLES]
            block_squares = sum(value * value for value in block)
            squares += block_squares
            rms = math.sqrt(block_squares / len(block))
            if rms <= self.NOISE_FLOOR_RMS:
                result = block  # Do not amplify silence or low-level noise.
            else:
                desired = min(self.MAX_GAIN, max(1.0, self.TARGET_RMS / rms))
                # Fast gain reduction; bounded rise to avoid a sudden quiet-
                # speech burst. Peak control does not throttle the whole block.
                alpha = -math.expm1(-len(block) / self.SAMPLE_RATE / self.GAIN_RISE_SECONDS)
                self.gain = (desired if desired < self.gain else
                             self.gain + alpha * (desired - self.gain))
                result = [self._limit(value * self.gain) for value in block]
                self.speech_samples += len(block)
                self.speech_input_squares += block_squares
                self.speech_output_squares += sum(value * value for value in result)
            output.extend(result)
        self.samples += len(samples)
        self.input_squares += squares
        self.output_squares += sum(value * value for value in output)
        self.input_peak = max(self.input_peak, max(map(abs, samples)))
        self.output_peak = max(self.output_peak, max(map(abs, output)))
        return struct.pack(f'<{len(output)}h', *output)

    def diagnostics(self):
        return dict(profile=self.PROFILE, gainDb=round(20 * math.log10(self.gain), 2),
                    inputPeak=self.input_peak, outputPeak=self.output_peak,
                    inputRms=round(math.sqrt(self.input_squares / max(1, self.samples)), 1),
                    outputRms=round(math.sqrt(self.output_squares / max(1, self.samples)), 1),
                    samples=self.samples, maxGainDb=round(20 * math.log10(self.MAX_GAIN), 2),
                    peakLimit=self.PEAK_LIMIT, targetRms=self.TARGET_RMS,
                    softKnee=self.SOFT_KNEE, compressedSamples=self.compressed_samples,
                    speechSamples=self.speech_samples,
                    speechInputRms=round(math.sqrt(self.speech_input_squares / max(1, self.speech_samples)), 1),
                    speechOutputRms=round(math.sqrt(self.speech_output_squares / max(1, self.speech_samples)), 1))
