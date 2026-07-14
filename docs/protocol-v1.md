# WatcheRobot SDK Control Protocol v1

## Transport and pairing

- Discovery defaults to `37021/UDP`; the WebSocket gateway defaults to `8766/TCP`.
- Python listens. The robot's `sdk.control.app` actively discovers and connects.
- Authentication uses the temporary six-digit code displayed for the current App session.
- One robot control session is accepted at a time. v1 uses plain `ws://` on a trusted LAN.

Handshake sequence:

1. Robot sends `sys.client.hello`.
2. Python acknowledges hello and sends `sys.sdk.authenticate` with `pairing_code`, protocol version, client name,
   and `command_id`.
3. Robot sends `sys.ack` or `sys.nack`, then `evt.sdk.ready` with identity, firmware, and capabilities.

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

The host must authenticate and receive the `audio.stream` capability. It then sends
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
