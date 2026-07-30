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
    source_pcm = b"".join(
        (12000 if index % 32 < 16 else -12000).to_bytes(2, "little", signed=True)
        for index in range(960)
    )
    frame.planes[0].update(source_pcm)
    packets = encoder.encode(frame) + encoder.encode(None)
    return bytes(packets[0])


def test_opus_decoder_returns_16khz_mono_pcm():
    decoder = OpusDecoder()
    pcm = decoder.decode(_encode_opus_packet()) + decoder.flush()

    assert len(pcm) == 320 * 2
    assert any(pcm)
    assert decoder.flush() == b""
