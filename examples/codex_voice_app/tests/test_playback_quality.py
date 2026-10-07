"""Signal tests are not a certificate of acoustic intelligibility."""
import math
import random
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from playback_gain import PlaybackGain


def pack(samples):
    return struct.pack(f'<{len(samples)}h', *samples)


def unpack(data):
    return list(struct.unpack(f'<{len(data) // 2}h', data))


def test_gain_reduction_is_continuous_not_a_packet_boundary_jump():
    gain = PlaybackGain(sample_rate=48000)
    for _ in range(100):
        gain.process(pack([1000] * 960))
    # Same sample at the boundary; block RMS changes due to later samples.
    first = unpack(gain.process(pack([1000] * 960)))
    second = unpack(gain.process(pack([1000] * 480 + [2000] * 480)))
    delay = gain.diagnostics()['lookaheadSamples']
    assert abs(second[delay] - first[-1]) < 100
    assert gain.diagnostics()['profile'] == 'clear-speech-v3'


def test_lookahead_peak_control_preserves_wave_shape_at_constant_gain():
    gain = PlaybackGain(sample_rate=48000)
    source = [round(3000 * math.sin(2 * math.pi * 500 * i / 48000)) for i in range(48000)]
    result = unpack(gain.process(pack(source)) + gain.flush())
    delay = gain.diagnostics()['lookaheadSamples']
    result = result[delay:delay + len(source)]
    # Ignore gain rise, then solve one linear gain against the same source.
    x, y = source[24000:], result[24000:]
    scalar = sum(a * b for a, b in zip(x, y)) / sum(a * a for a in x)
    error = sum((b - scalar * a) ** 2 for a, b in zip(x, y))
    power = sum(b * b for b in y)
    assert math.sqrt(error / power) < .005
    assert math.sqrt(power / len(y)) >= 8500
    assert max(map(abs, result)) <= 29000


def test_limiter_survives_transients_and_extreme_sign_changes_without_clipping():
    gain = PlaybackGain(sample_rate=48000)
    gain.process(pack([1000] * 48000))
    source = [1000] * 480 + [32767, -32768] * 100 + [1000] * 960
    result = unpack(gain.process(pack(source)) + gain.flush())
    assert max(map(abs, result)) <= 29000
    assert gain.diagnostics()['limitedSamples'] > 0


def test_quality_dsp_does_not_depend_on_packet_partition():
    samples = [round(2000 * math.sin(i / 9) + 500 * math.sin(i / 3)) for i in range(5000)]
    whole, split = PlaybackGain(sample_rate=48000), PlaybackGain(sample_rate=48000)
    expected = whole.process(pack(samples)) + whole.flush()
    sizes, pieces, index = [37, 121, 960, 243, 17], [], 0
    while index < len(samples):
        size = sizes[len(pieces) % len(sizes)]
        pieces.append(split.process(pack(samples[index:index + size])))
        index += size
    actual = b''.join(pieces) + split.flush()
    assert actual == expected


def test_invalid_rate_or_data_does_not_change_quality_state():
    with pytest.raises(ValueError):
        PlaybackGain(sample_rate=44100)
    gain = PlaybackGain(sample_rate=48000)
    before = gain.diagnostics()
    with pytest.raises(ValueError):
        gain.process(b'x')
    assert before == gain.diagnostics()


def test_limiting_and_noise_transitions_are_partition_independent():
    samples = ([80] * 731 + [1000] * 2901 + [32767, -32768] * 503 +
               [0] * 1237 + [2000] * 407 + [-80] * 999)
    whole, split = PlaybackGain(sample_rate=48000), PlaybackGain(sample_rate=48000)
    expected = whole.process(pack(samples)) + whole.flush()
    pieces = [split.process(pack(samples[i:i + 37])) for i in range(0, len(samples), 37)]
    assert b''.join(pieces) + split.flush() == expected
    assert whole.diagnostics() == split.diagnostics()
    assert whole.diagnostics()['limitedSamples'] > 0
    assert len(whole.delay) == whole.lookahead
    assert len(whole.ceilings) <= whole.lookahead + 1


@pytest.mark.parametrize('rate', [24000, 48000])
def test_limiter_matches_independent_window_scan_at_delay_boundaries(rate):
    probe = PlaybackGain(sample_rate=rate)
    delay = probe.lookahead
    rng = random.Random(731)
    source = [1000] * (delay * 2 + 31)
    for i in [delay - 1, delay, delay + 1, delay * 2, delay * 2 + 30]:
        source[i] = rng.choice([32767, -32768])
    source += [rng.randrange(-32768, 32768) for _ in range(97)]
    boosted, actual = [], []
    for sample in source + [0] * delay:
        actual += unpack(probe.process(pack([sample])))
        boosted.append(sample * probe.gain)
    expected, envelope = [0] * delay, 1.0
    for i, value in enumerate(boosted[:len(source)]):
        # Independent O(N*L) oracle rather than a second monotonic deque.
        ceiling = min(1.0, *(
            (min(1.0, 29000 / abs(v)) if v else 1.0) + (j - i) / delay
            for j, v in enumerate(boosted[i:i + delay + 1], i)))
        released = envelope + probe.release_alpha * (1 - envelope)
        envelope = min(ceiling, released, 29000 / abs(value) if value else 1.0)
        expected.append(round(value * envelope))
    assert actual == expected
    assert max(map(abs, actual)) <= 29000
    assert len(probe.delay) <= delay
    assert len(probe.ceilings) <= delay + 1


def test_flush_is_terminal_idempotent_and_preserves_short_tail():
    gain = PlaybackGain(sample_rate=48000)
    source = pack([1000, -1000])
    beginning = gain.process(source)
    tail = gain.flush()
    assert len(beginning + tail) == len(source) + gain.lookahead * 2
    assert unpack(beginning + tail)[gain.lookahead:] == [1003, -1006]
    before = gain.diagnostics()
    assert gain.flush() == b''
    assert gain.diagnostics() == before
    with pytest.raises(RuntimeError, match='finalized'):
        gain.process(source)
