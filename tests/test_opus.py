import av

from watcherobot.opus import OpusDecoder


def _encode_opus_packet() -> bytes:
    encoder = av.CodecContext.create("opus", "w")
    encoder.sample_rate = 16000
    encoder.layout = "mono"
    encoder.format = "s16"
    encoder.bit_rate = 16000
    encoder.open()

    frame = av.AudioFrame(format="s16", layout="mono", samples=960)
    frame.sample_rate = 16000
    frame.planes[0].update(b"\x00\x00" * 960)
    packets = encoder.encode(frame) + encoder.encode(None)
    return bytes(packets[0])


def test_opus_decoder_returns_16khz_mono_pcm():
    decoder = OpusDecoder()
    pcm = decoder.decode(_encode_opus_packet()) + decoder.flush()

    assert pcm
    assert len(pcm) % 2 == 0
    assert decoder.flush() == b""
