# WatcheRobot SDK Control Protocol v1

## Transport and pairing

- Discovery: `37021/UDP` by default.
- Gateway: `8766/TCP` WebSocket by default.
- The Python SDK listens; `sdk.control.app` on the robot actively discovers and connects.
- Authentication uses the six-digit code displayed for the current app session.
- One WebSocket control session is accepted at a time.

Discovery probe sent by the robot:

```json
{"cmd":"SDK_DISCOVER","service":"watcher-sdk","protocol_version":"1.0","device_id":"watcher-1234","mac":"..."}
```

Python response:

```json
{"cmd":"ANNOUNCE","service":"watcher-sdk","protocol_version":"1.0","port":8766,"server":"watcherobot-python-sdk"}
```

The WebSocket handshake is:

1. Robot sends the existing `sys.client.hello` message.
2. Python sends `sys.ack` for `sys.client.hello`.
3. Python sends `sys.sdk.authenticate` with `pairing_code`, `protocol_version`, `client_name`, and `command_id`.
4. Robot sends `sys.ack` or `sys.nack`, then `evt.sdk.ready` with device, firmware, and capability data.

## JSON envelope

Commands keep the existing Watcher envelope:

```json
{
  "type": "ctrl.behavior.play",
  "code": 0,
  "data": {
    "command_id": "client-generated-id",
    "behavior_id": "greeting",
    "repeat": 1
  }
}
```

Successful acceptance:

```json
{
  "type": "sys.ack",
  "code": 0,
  "data": {
    "type": "ctrl.behavior.play",
    "command_id": "client-generated-id",
    "operation_id": 42
  }
}
```

`sys.ack` means accepted, not completed. Finite operation state is reported separately:

```json
{
  "type": "evt.sdk.operation",
  "code": 0,
  "data": {
    "operation_id": 42,
    "domain": "behavior",
    "state": "completed"
  }
}
```

States are `starting`, `running`, `completed`, `failed`, and `cancelled`. A failed event may include `error_code`.
Commands are deduplicated by `command_id` in a bounded device cache.

The device validates v1 commands strictly. Identifiers that exceed the fixed protocol limits are rejected rather
than truncated, the pairing code must be exactly six digits, and numeric/enum fields must be within their documented
ranges. A correlatable malformed command receives `invalid_argument`; an unknown command type receives
`unsupported_command`; a saturated device command queue receives `command_queue_full`.

For `ctrl.motion.move_to`, `completed` means the STM32 reported `MOTION_DONE` for the matching command sequence.
`stopped` or `interrupted` maps to `cancelled`; MCU rejection, fault, or terminal-event timeout maps to `failed`.
This is execution-timeline completion, not physical position-feedback convergence.

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
| `ctrl.audio.play` | `sound_id` | Job |
| `ctrl.audio.stop` | — | ACK |
| `ctrl.light.set` | `color`, `brightness`, `zone` | ACK |
| `ctrl.light.effect.play` | `effect`, color fields, `period_ms`, `repeat` | Job |
| `ctrl.light.off` | — | ACK |
| `ctrl.microphone.open` | `sample_rate_hz` | `session_id` ACK |
| `ctrl.microphone.close` | `session_id` | ACK |
| `ctrl.camera.capture` | optional `width`, `height`, `quality` | `session_id` ACK + JPEG frame |
| `ctrl.job.cancel` | `operation_id` | ACK + cancelled event |

## WSPK media frames

ESP32-to-Python media currently uses the compatible 14-byte little-endian header:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 4 | ASCII `WSPK` |
| 4 | 1 | frame type: audio `1`, image `3` |
| 5 | 1 | flags: first `1`, last `2`, keyframe `4`, fragment `8` |
| 6 | 4 | sequence |
| 10 | 4 | payload length |

The Python parser also accepts the current 16-byte header containing a two-byte `stream_id` before `sequence`.
When present, that ID is matched to the acknowledged microphone/camera session so delayed frames from an older
session are ignored. The 14-byte compatibility header has implicit stream ID `0`. Audio is PCM S16LE/16 kHz/mono.
A camera capture returns one complete JPEG image frame.
