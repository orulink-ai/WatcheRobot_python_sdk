# Microphone audio contract

## What a normal Application receives

The device sends microphone audio as Opus: 16 kHz, mono, 60 ms per packet,
with one complete packet in each WSPK audio frame. `ApplicationContext.robot`
decodes those packets before exposing `MicrophoneSession` frames. Therefore:

- `robot.microphone.open_pcm()` and `robot.microphone.open()` return 16 kHz,
  mono, signed-16-bit little-endian PCM frames.
- `robot.microphone.record_pcm()` and `robot.microphone.record()` return an
  `AudioRecording` that can be saved as a valid WAV file.
- `AudioRecording.duration_seconds` is calculated from decoded PCM bytes, not
  the compressed Opus packet size.

```python
with app.robot.microphone.open_pcm() as microphone:
    pcm = microphone.read(timeout=1.0).data

recording = app.robot.microphone.record_pcm(duration=2.0)
assert recording.format.encoding == "pcm_s16le"
recording.save("recording.wav")
```

## Responsibility boundary

The Runtime/Daemon owns the physical device WebSocket, WSPK framing,
connection lifecycle, and source-aware routing. It must keep device media
payloads opaque when forwarding them to an Application; it does not decode,
transcode, or inspect Opus payload bytes.

The SDK running inside the managed Application owns the high-level media API
and performs Opus-to-PCM decoding. This is why the desktop installation ships
the complete shared SDK environment for the Daemon and Applications, even
though the desktop UI itself only invokes the Daemon control API.

An Application that needs raw frames must opt into the advanced
`ApplicationChannels` Device channel and process the WSPK payload itself.

## Failure handling

Malformed packets are dropped without terminating the Device channel. Their
count is available as `MicrophoneSession.decode_failures`; queue overflow is
reported separately as `MicrophoneSession.dropped_frames`.
