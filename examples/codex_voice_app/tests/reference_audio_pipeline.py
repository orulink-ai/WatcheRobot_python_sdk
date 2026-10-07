"""Frozen v2 A/B rendering, for tests only; never imported by production."""
from fractions import Fraction
import math
import struct

import av

from reference_playback_v2 import PlaybackGain as OldGain
from playback_gain import PlaybackGain


def resample(pcm, source_rate, target_rate):
    frame = av.AudioFrame(format='s16', layout='mono', samples=len(pcm) // 2)
    frame.sample_rate = source_rate
    frame.planes[0].update(pcm)
    converter = av.AudioResampler(format='s16', layout='mono', rate=target_rate)
    frames = [*converter.resample(frame), *converter.resample(None)]
    return b''.join(bytes(item.planes[0])[:item.samples * 2] for item in frames)


def render_v2(pcm48):
    pcm24 = resample(pcm48, 48000, 24000)
    gain = OldGain()
    converter = av.AudioResampler(format='flt', layout='mono', rate=48000)
    output, index, limited = [], 0, 0

    def admit(frames):
        nonlocal limited
        for frame in frames:
            values = [v * 32768 for (v,) in struct.iter_unpack(
                '=f', bytes(frame.planes[0])[:frame.samples * 4])]
            peak = max(map(abs, values), default=0)
            scale = min(1, 29000 / peak) if peak else 1
            limited += scale < 1
            output.append(struct.pack(f'<{len(values)}h', *(round(v * scale) for v in values)))

    for offset in range(0, len(pcm24), 960):
        chunk = gain.process(pcm24[offset:offset + 960])
        frame = av.AudioFrame(format='s16', layout='mono', samples=len(chunk) // 2)
        frame.sample_rate, frame.pts, frame.time_base = 24000, index, Fraction(1, 24000)
        frame.planes[0].update(chunk)
        index += frame.samples
        admit(converter.resample(frame))
    admit(converter.resample(None))
    return b''.join(output), {**gain.diagnostics(), 'postSrcLimitedFrames': limited}


def render_v3(pcm48):
    gain = PlaybackGain(sample_rate=48000)
    output = b''.join(gain.process(pcm48[i:i + 1920]) for i in range(0, len(pcm48), 1920))
    output += gain.flush()
    return output[gain.lookahead * 2:gain.lookahead * 2 + len(pcm48)], gain.diagnostics()


def summary(pcm, *, mask=None):
    values = [v for (v,) in struct.iter_unpack('<h', pcm)]
    if mask is not None:
        values = [v for v, active in zip(values, mask) if active]
    return dict(samples=len(values), rms=round(math.sqrt(sum(v*v for v in values) / max(1, len(values))), 2),
                peak=max(map(abs, values), default=0))
