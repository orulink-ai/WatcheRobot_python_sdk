# WatcheRobot SDK Control Protocol v1

## Transport and pairing

- Discovery defaults to `37021/UDP`; the WebSocket gateway defaults to `8766/TCP`.
- Python listens. The robot's `sdk.control.app` actively discovers and connects.
- Authentication uses the temporary six-digit code displayed for the current App session.
- One robot control session is accepted at a time. v1 uses plain `ws://` on a trusted LAN.

Discovery: the robot broadcasts `SDK_DISCOVER` with its temporary pairing code and request ID; only the matching Python
service replies with `ANNOUNCE`, echoing both values. The WebSocket hello repeats the pairing-code check before a
control session becomes ready.

Handshake sequence:

1. Robot sends `sys.client.hello` with its six-digit `pairing_code`.
2. Python validates that code and replies with `sys.ack(type=sys.client.hello)`; it returns `sys.nack` and closes the
   WebSocket when the code does not match.
3. After the acknowledged hello, the robot sends `evt.sdk.ready` with identity, firmware, and capabilities.

## JSON envelope and Jobs

Commands reuse `{type, code, data}` and include a client-generated `command_id`:

```json
{
  "type": "ctrl.behavior.play",
  "code": 0,
  "data": {"command_id": "cmd-1", "behavior_id": "happy", "repeat": 1}
}
```

ACK means accepted, not completed. Finite operations return `operation_id`; their lifecycle is reported separately
through `evt.sdk.operation` as `starting`, `running`, then `completed`, `failed`, or `cancelled`. Commands are
deduplicated by a bounded device-side `command_id` cache.

Firmware parsing is strict: identifiers are rejected instead of truncated, the pairing code is exactly six digits,
and numeric/enum fields must be in range. Correlatable errors use `invalid_argument`, `unsupported_command`,
`command_queue_full`, or a domain-specific reason.

For `ctrl.motion.move_to`, completed means STM32 reported `MOTION_DONE` for the matching sequence. It confirms the
execution timeline, not closed-loop physical position convergence.

## Commands

| Type | Key data | Result |
| --- | --- | --- |
| `ctrl.behavior.play` | `behavior_id`, `repeat` | Job |
| `ctrl.behavior.stop` | — | ACK |
| `ctrl.animation.play` | `animation_id` | Job |
| `ctrl.animation.stop` | — | ACK |
| `ctrl.motion.move_to` | `pan_deg`, `tilt_deg`, `duration_ms`, `profile` | Job |
| `ctrl.motion.set_target` | one or both of `pan_deg`, `tilt_deg` | ACK |
| `ctrl.motion.action.play` | `action_id` | Job |
| `ctrl.motion.stop` | — | ACK |
| `ctrl.audio.play` | installed `sound_id` | Job |
| `ctrl.audio.stream.begin` | stream ID, PCM format, byte count, SHA256 | ACK |
| `ctrl.audio.stop` | — | ACK |
| `ctrl.light.set` | `color`, `brightness`, `zone` | ACK |
| `ctrl.light.effect.play` | effect and timing fields | Job |
| `ctrl.light.off` | — | ACK |
| `ctrl.microphone.open` | `sample_rate_hz` | `session_id` ACK |
| `ctrl.microphone.close` | `session_id` | ACK |
| `ctrl.camera.capture` | optional `width`, `height`, `quality` | `session_id` ACK + JPEG |
| `ctrl.job.cancel` | `operation_id` | ACK + cancelled event |

## WSPK media frames

The current little-endian header is 16 bytes:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 4 | ASCII `WSPK` |
| 4 | 1 | audio `1`, image `3` |
| 5 | 1 | first `1`, last `2`, keyframe `4`, fragment `8` |
| 6 | 2 | `stream_id` |
| 8 | 4 | sequence |
| 12 | 4 | payload length |

The parser also accepts the legacy 14-byte header with implicit stream ID zero for compatible device-to-host media.
Microphone audio is PCM S16LE/16 kHz/mono. Camera capture returns one JPEG. Current 16-byte frames correlate it to
the acknowledged session ID; legacy stream-zero frames remain compatible but cannot strongly reject a late image
from a timed-out capture. A JPEG may be carried in one frame or in ordered frames marked with `fragment`; fragmented
images use `first`/`last`, are reassembled with an 8 MiB bound, and are discarded on sequence gaps.

### Host-to-robot audio

The host must complete the pairing-code hello validation and receive the `audio.stream` capability. It then sends
`ctrl.audio.stream.begin` with:

- a non-zero 16-bit `stream_id`;
- `total_bytes` and `audio_sha256`;
- the fixed v1 format: PCM S16LE, 24 kHz, mono.

Only the authorized stream is accepted by the SDK App's WSPK guard. Data uses 4096-byte host payloads, sequence
starting at zero, and FIRST/LAST flags. The Python sender keeps a bounded window derived from
`evt.audio.buffer_status.pending_frames` and `queue_depth`.

The begin ACK means authorized, not played. Playback completes only when `evt.audio.buffer_status` reports
`complete` for the same non-zero stream ID and SHA256 matches. `aborted` means cancelled. Queue, sequence,
stale-stream, or hardware-write errors mean failed.

`ctrl.audio.stop`, replacement, disconnect, session reset, or App close stops host production, revokes device
authorization, clears the playback queue, and terminates the previous playback. Binary audio received before
authentication or without a matching begin command is rejected.

## Physical input events

After authentication, firmware advertises `input.back_touch`, `input.screen_touch`, and `input.roller`. It sends
physical interactions as `evt.sdk.input`; delivery is ordered but not durable:

```json
{"type":"evt.sdk.input","code":0,"data":{"source":"back_touch","action":"press","touch_id":0,"timestamp_ms":1234}}
{"type":"evt.sdk.input","code":0,"data":{"source":"screen_touch","action":"tap","x":120,"y":180,"timestamp_ms":1250}}
{"type":"evt.sdk.input","code":0,"data":{"source":"roller","action":"rotate","delta":-1,"timestamp_ms":1280}}
```

Back touch supports `press`, `release`, and the protocol-reserved `long_press`; current hardware normally reports
press/release for touch ID zero. Screen input reports a short tap with display coordinates. Roller `delta` is signed
and may combine several steps. Roller press is intentionally absent: short press owns the local exit UI and long
hold owns system shutdown. The Python side validates all fields and keeps a bounded newest-64 event queue.
