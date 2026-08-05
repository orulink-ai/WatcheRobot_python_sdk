# Microphone audio contract

## What a normal Application receives

The device sends microphone audio as Opus: 16 kHz, mono, 60 ms per packet,
with one complete packet in each WSPK audio frame. `ApplicationContext.robot`
decodes those packets before exposing `MicrophoneSession` frames. Therefore:

- `robot.microphone.open_pcm()` returns 16 kHz, mono, signed-16-bit
  little-endian PCM frames.
- `robot.microphone.record_pcm()` returns an `AudioRecording` that can be
  saved as a valid WAV file. Its `duration_seconds` is calculated from decoded
  PCM bytes, not the compressed Opus packet size.
- `robot.microphone.open()` retains the raw Opus-packet stream for Applications
  that process the media protocol themselves. Its `AudioFormat.encoding` is
  `"opus"`, and it has no PCM byte width or byte-derived duration.
- `robot.microphone.record()` rejects raw Opus recording with a migration
  error. It must not concatenate or truncate compressed packets and label the
  result as a PCM WAV file; use `open()` for packets or `record_pcm()` for a
  recordable PCM result.

```python
with app.robot.microphone.open_pcm() as microphone:
    pcm = microphone.read(timeout=1.0).data

recording = app.robot.microphone.record_pcm(duration=2.0)
assert recording.format.encoding == "pcm_s16le"
recording.save("recording.wav")
```

## Responsibility boundary

The opposite direction uses `robot.audio.open_stream()` for 24 kHz mono
PCM16. Robot-microphone upload and robot-speaker playback are real-time but
half-duplex: opening either direction first closes the other. Camera preview
does not participate in this arbitration.

The Runtime/Daemon owns the physical device WebSocket, WSPK framing,
connection lifecycle, and source-aware routing. It must keep device media
payloads opaque when forwarding them to an Application; it does not decode,
transcode, or inspect Opus payload bytes.

The SDK running inside the managed Application owns the high-level media API
and performs Opus-to-PCM decoding. This is why the desktop installation ships
the complete shared SDK environment for the Daemon and Applications, even
though the desktop UI itself only invokes the Daemon control API.

An Application that needs full WSPK frames or a custom Device-channel protocol
can opt into the advanced `ApplicationChannels` Device channel and process the
payload itself. Applications that only need raw microphone packets can use
`robot.microphone.open()`.

## Failure handling

Malformed packets are dropped without terminating the Device channel. Their
count is available as `MicrophoneSession.decode_failures` and is copied to the
`AudioRecording.decode_failures` returned by `record_pcm()`; queue overflow is
reported separately as `MicrophoneSession.dropped_frames` and
`AudioRecording.dropped_frames`.
