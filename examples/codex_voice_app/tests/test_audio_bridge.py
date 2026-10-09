import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from audio_bridge import OutputBuffer


def chunk(data, rate=24000, channels=1):
    return {'data': base64.b64encode(data).decode(), 'sampleRate': rate,
            'numChannels': channels, 'samplesPerChannel': len(data) // (2 * channels)}


def test_chunks_are_played_as_one_utterance_not_restarted_per_delta():
    buffer = OutputBuffer()
    buffer.add(chunk(b'\x01\x00' * 1200))
    buffer.add(chunk(b'\x02\x00' * 1200))
    assert buffer.take() == b'\x01\x00' * 1200 + b'\x02\x00' * 1200
    assert not buffer.pending


def test_resamples_actual_pcm_to_device_format():
    buffer = OutputBuffer()
    buffer.add(chunk(b'\x00\x00' * 1600, rate=16000))
    pcm = buffer.take()
    assert abs(len(pcm) - 4800) <= 2
    assert set(pcm) == {0}


@pytest.mark.parametrize('audio', [chunk(b'x'), chunk(b'xx', rate=0),
                                  {'data': '!!', 'sampleRate': 24000, 'numChannels': 1}])
def test_rejects_invalid_format_or_base64(audio):
    with pytest.raises(ValueError):
        OutputBuffer().add(audio)


def test_overflow_and_mid_utterance_format_change_fail_explicitly():
    buffer = OutputBuffer(max_seconds=1)
    buffer.add(chunk(b'\x00\x00' * 24000))
    with pytest.raises(ValueError, match='limit'):
        buffer.add(chunk(b'xx'))
    with pytest.raises(ValueError, match='format'):
        buffer.add(chunk(b'xx', rate=16000))


def test_native_48k_take_is_bit_exact_and_default_legacy_remains_24k():
    pcm = b'\x01\x00' * 960
    buffer = OutputBuffer()
    buffer.add(chunk(pcm, rate=48000))
    assert buffer.take(sample_rate=48000) == pcm
    buffer.add(chunk(pcm, rate=48000))
    assert abs(len(buffer.take()) - 960) <= 2


def test_bad_target_rate_preserves_pending_audio():
    buffer = OutputBuffer()
    buffer.add(chunk(b'\x01\x00' * 960, rate=48000))
    with pytest.raises(ValueError):
        buffer.take(sample_rate=44100)
    assert buffer.pending
    assert buffer.take(sample_rate=48000) == b'\x01\x00' * 960
