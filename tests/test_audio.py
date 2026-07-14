import wave

import pytest

from watcherobot.audio import OUTPUT_AUDIO_FORMAT, load_pcm_wave


def _write_wave(path, *, sample_rate=24000, channels=1, sample_width=2, pcm=b"\x01\x00\x02\x00"):
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setframerate(sample_rate)
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.writeframes(pcm)


def test_load_pcm_wave_accepts_device_output_format(tmp_path):
    path = tmp_path / "voice.wav"
    _write_wave(path)

    audio = load_pcm_wave(path)

    assert audio.data == b"\x01\x00\x02\x00"
    assert audio.audio_format == OUTPUT_AUDIO_FORMAT


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sample_rate", 16000, "24000 Hz"),
        ("channels", 2, "mono"),
        ("sample_width", 1, "16-bit"),
    ],
)
def test_load_pcm_wave_rejects_unsupported_format(tmp_path, field, value, message):
    options = {"sample_rate": 24000, "channels": 1, "sample_width": 2}
    options[field] = value
    path = tmp_path / "bad.wav"
    _write_wave(path, **options)

    with pytest.raises(ValueError, match=message):
        load_pcm_wave(path)
