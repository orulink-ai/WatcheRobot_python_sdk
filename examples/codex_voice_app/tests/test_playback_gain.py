import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from playback_gain import PlaybackGain


def pcm(value, samples=480):
    return struct.pack('<h', value) * samples


def values(data):
    return [item[0] for item in struct.iter_unpack('<h', data)]


def test_quiet_speech_is_boosted_with_bounded_smooth_gain():
    gain = PlaybackGain()
    first = gain.process(pcm(1000))
    assert 1000 < max(values(first)) < 4000
    for _ in range(100):
        output = gain.process(pcm(1000))
    assert 11500 <= max(values(output)) <= 12500
    assert gain.diagnostics()['gainDb'] <= 24.09
    assert gain.diagnostics()['outputRms'] > gain.diagnostics()['inputRms']


def test_silence_and_quiet_noise_are_not_amplified():
    gain = PlaybackGain()
    for _ in range(50):
        gain.process(pcm(1000))
    silence = values(gain.process(pcm(0)))
    assert silence[gain.lookahead:] == [0] * (480 - gain.lookahead)
    assert PlaybackGain().process(pcm(80))[gain.lookahead * 2:] == pcm(80, 480 - gain.lookahead)
    # After the smooth envelope settles, quiet noise returns to unity.
    for _ in range(100):
        output = values(gain.process(pcm(80)))
    assert max(output) <= 81


def test_already_loud_source_is_not_turned_down_by_loudness_compensation():
    gain = PlaybackGain()
    output = gain.process(pcm(16000)) + gain.flush()
    assert output[gain.lookahead * 2:] == pcm(16000)
    assert gain.diagnostics()['gainDb'] == 0


def test_peak_headroom_prevents_clipping_after_quiet_speech():
    gain = PlaybackGain()
    for _ in range(100):
        gain.process(pcm(1000))
    samples = pcm(1000, 478) + struct.pack('<hh', 32767, -32768)
    result = values(gain.process(samples) + gain.flush())
    assert max(map(abs, result)) <= 29000
    assert result[-1] < 0 < result[-2]
    assert math.isfinite(gain.diagnostics()['gainDb'])


def test_invalid_pcm_fails_before_mutating_gain():
    gain = PlaybackGain()
    before = gain.diagnostics()
    with pytest.raises(ValueError):
        gain.process(b'x')
    assert gain.diagnostics() == before
    assert gain.process(b'') == b''


def test_maximum_speech_profile_reaches_useful_loudness_not_old_fourfold_cap():
    gain = PlaybackGain()
    for _ in range(100):
        output = values(gain.process(pcm(1000)))
    assert 11500 <= max(output) <= 12500
    assert gain.diagnostics()['profile'] == 'clear-speech-v3'
    assert gain.diagnostics()['maxGainDb'] >= 24


def test_short_transient_does_not_turn_down_the_entire_speech_frame():
    gain = PlaybackGain()
    for _ in range(100):
        gain.process(pcm(1000))
    output = values(gain.process(pcm(1000, 479) + struct.pack('<h', 32767)) + gain.flush())
    # Limit only the lookahead region, not the entire packet.
    assert min(output[:240]) >= 11000
    assert max(map(abs, output)) <= 29000
    assert gain.diagnostics()['limitedSamples'] > 0


def test_linear_lookahead_limiter_is_sign_symmetric_and_bounds_peaks():
    gain = PlaybackGain()
    for _ in range(100):
        gain.process(pcm(1000))
    source = [2000, 3000, 4000, 5000] * 50
    result = values(gain.process(struct.pack(f'<{len(source)}h', *source)) + gain.flush())[gain.lookahead:]
    # Independent fresh, identical state verifies sign symmetry.
    negative = PlaybackGain()
    for _ in range(100):
        negative.process(pcm(-1000))
    inverse = values(negative.process(struct.pack(f'<{len(source)}h', *(-x for x in source))) + negative.flush())[negative.lookahead:]
    assert inverse == [-x for x in result]
    assert result[:4] == sorted(result[:4])
    assert len(set(result[:4])) == 4
    assert max(result) <= 29000


def test_large_input_uses_bounded_control_blocks_not_one_peak_for_all_speech():
    source = pcm(1000, 24000)
    batched = PlaybackGain()
    streamed = PlaybackGain()
    whole = batched.process(source)
    pieces = b''.join(streamed.process(source[i:i + 960]) for i in range(0, len(source), 960))
    assert whole == pieces
    assert batched.diagnostics() == streamed.diagnostics()


def test_speech_only_energy_excludes_long_silence_from_loudness_summary():
    gain = PlaybackGain()
    gain.process(pcm(1000))
    gain.process(pcm(0, gain.lookahead))
    before = gain.diagnostics()
    gain.process(pcm(0, 24000))
    after = gain.diagnostics()
    assert after['speechOutputRms'] == before['speechOutputRms']
    assert after['speechSamples'] == before['speechSamples']
    assert after['outputRms'] < before['outputRms']
