"""Bundled, non-private reference utterance. No live recording or cloud TTS."""
from pathlib import Path
import asyncio
import wave

import av

TEST_AUDIO_PATH = Path(__file__).with_name('audio') / 'speaker-reference.wav'


def load_test_audio():
    with wave.open(str(TEST_AUDIO_PATH), 'rb') as source:
        if (source.getnchannels() != 1 or source.getsampwidth() != 2
                or not 0 < source.getnframes() <= source.getframerate() * 15):
            raise ValueError('Invalid bundled speaker reference')
        frame = av.AudioFrame(format='s16', layout='mono', samples=source.getnframes())
        frame.sample_rate = source.getframerate()
        frame.planes[0].update(source.readframes(source.getnframes()))
    # Convert this fixed asset once. Live Codex PCM is already 48 kHz.
    converter = av.AudioResampler(format='s16', layout='mono', rate=48000)
    frames = [*converter.resample(frame), *converter.resample(None)]
    return b''.join(bytes(f.planes[0])[:f.samples * 2] for f in frames)


async def send_test_audio(peer, gain, pcm, progress):
    for offset in range(0, len(pcm), 1920):
        # Only the fixed-source test uses a bounded 120ms prefill. Live Codex
        # arrivals are not replaced with a pre-generated/fully queued utterance.
        while len(peer.track.buffer) >= 11520:
            await asyncio.sleep(.005)
        chunk = pcm[offset:offset + 1920]
        await peer.append_audio(gain.process(chunk), sample_rate=48000)
        if offset % 24000 == 0:
            progress('playing', round((offset + len(chunk)) / 96))
        await asyncio.sleep(0)
    tail = gain.flush()
    if tail:
        await peer.append_audio(tail, sample_rate=48000)
    progress('draining', round(len(pcm) / 96))
    while peer.track.buffer:
        await asyncio.sleep(.02)
    await asyncio.sleep(.3)  # Transport tail, not a device render receipt.
