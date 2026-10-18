# Continuous PCM playback

Firmware advertising `audio.stream.continuous.v1` accepts one bounded PCM stream
for an entire reply. It keeps the speaker pipeline open across individual TTS
segments, instead of ending and restarting playback at each sentence. This is
independent of ASR and of `expression.audio_follow.local_pcm.v1` mouth following.

## API and ownership

`robot.audio.open_stream(max_bytes=4 * 1024 * 1024)` returns a
`ContinuousAudioPlayback`, a Job-compatible incremental writer. The format is
24 kHz, mono, signed 16-bit little-endian PCM. Every write must contain complete
samples. Await each returned concurrent Future before submitting another write;
there is no unbounded internal write queue.

```python
import asyncio

playback = await asyncio.to_thread(robot.audio.open_stream)
try:
    async for pcm in generated_pcm:
        await asyncio.wrap_future(playback.write(pcm))
    await asyncio.wrap_future(playback.finish())
    await asyncio.to_thread(playback.wait, playback.expected_duration_seconds + 20)
except BaseException:
    await asyncio.to_thread(playback.cancel)
    raise
```

The source iterator stays on the Application's own event loop. Only immutable
PCM bytes cross to the SDK transport loop. The asynchronous voice adapter also
provides `SDKVoiceDevice.supports_continuous_playback` and
`await device.play_stream(frames)`, including source closure and confirmed stop
on cancellation/failure. A successful stream does not need an extra stop.

A live or failed-but-not-stopped continuous owner rejects another audio stream,
local sound or v1 playback. Explicit stop must succeed before ownership can be
reused; a failed stop can be retried. Cancelling cannot retract sound already
played. The total stream limit is 4 MiB (about 87 seconds), independent of any
Application preparation buffer. Over-budget writes fail rather than truncating.

## Wire contract

The begin command explicitly carries `mode: "continuous"`, `max_bytes` and the
format. It omits v1 `total_bytes` and `audio_sha256`; placeholder sizes and fake
hashes are not valid substitutes. The transport aggregates small writes into
4096-byte slots, retains a shorter tail, and sends the tail plus a unique empty
LAST at `finish`. FIRST is unique, sequence numbers increase, and no device
playback stream is opened for each sentence.

The writer establishes the incremental SHA256 before sending LAST. Completion
requires the actual device hash to match and reported dropped/timeout/sequence
gap counters to remain zero. Receiving a complete frame/hash alone does not
prove the physical speaker played every sample. Firmware performs its normal
DMA drain only at the end of the reply. Connection loss and source failure
terminate the stream through the existing stop/ownership path.

Daemon forwarding remains opaque. All commands and media pass through the
Application's injected Device channel. Both managed-Application and Desktop
transports share the continuous sender and retain credit/watermark backpressure;
see [audio credit accounting](audio-credit-window.md). Continuous streams can
replenish up to 32 packets per snapshot, still bounded by target occupancy,
reported free slots and unacknowledged in-flight packets. Legacy streams retain
their eight-packet cap; device queue size and initial four credits are unchanged.

## Compatibility and measurement

Old firmware keeps strict v1 behavior. The Pi example starts its first prepared
segment immediately and can coalesce already prepared later segments on that
path. Only the new negotiated capability enables continuous playback.

The Pi preparation queue is bounded by eight reservations and 8 MiB, including
synthesis in progress. It continues generating while there is budget. It still
collects one synthesized segment before enqueueing it; this does not claim
cloud-first-chunk playback. A slower provider or network can still cause an
underrun even with a single stream.

`SDKVoiceDevice.last_playback_stats` reports begin, actual transport first-packet
send, upload, completion, exceptional stop and total elapsed milliseconds. These
are control/transport observations, not acoustic first-sample timestamps.
Operation-preface-to-result waiting includes model/tool work; it is separate
from gaps within a final reply. Hardware comparisons should reuse identical PCM
and separately check stream count, device counters and audible continuity.
