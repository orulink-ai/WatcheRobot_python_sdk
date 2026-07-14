# WatcheRobot Python SDK

English | [简体中文](README.zh-CN.md)

`watcherobot` is the synchronous desktop/LAN SDK for WatcheRobot. The computer starts a UDP discovery service and
WebSocket gateway; the robot's **SDK Control App** discovers it and connects back.

> v0.1 targets trusted local networks. It uses plain `ws://`, a temporary six-digit pairing code, and one robot per
> SDK instance.

## Install

The current public preview is `0.1.0a2` on TestPyPI. TestPyPI does not mirror every dependency, so install the
runtime dependency from PyPI first and the SDK itself from TestPyPI without dependency resolution:

```bash
python -m pip install "websockets>=12,<16"
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps watcherobot==0.1.0a2
```

For development or evaluation from this repository:

```bash
python -m pip install -e .
```

The stable PyPI package has not been published yet. After its release, install with:

```bash
python -m pip install watcherobot
```

Python 3.10 or newer is required.

## Compatibility

| Python SDK | Protocol | Verified ESP32 firmware | Python | Release status |
|---|---|---|---|---|
| `0.1.0a2` | `1.0` | `V3.1` with SDK Control App | `>=3.10` (CI: 3.10 / 3.11 / 3.12) | Alpha / TestPyPI |

After connecting, inspect `robot.device_info` and `robot.capabilities`; the negotiated device response is the source
of truth. Firmware older than `V3.1` is not currently covered by the compatibility promise.

## First connection

1. Connect the computer and robot to the same LAN.
2. Open **SDK Control App** from the robot launcher.
3. Note the temporary six-digit code shown on the robot.
4. Run the minimal connection example:

```bash
python examples/hello_robot.py
```

The minimal example connects, prints device information, and plays the factory `happy` Behavior:

```python
from watcherobot import WatcheRobot

with WatcheRobot.connect(pairing_code="123456") as robot:
    print(robot.device_info)
    robot.behavior.play("happy", repeat=1).wait(timeout=20)
```

## Capability examples

- `examples/quickstart.py`: directly call the main SDK domains in one file, with confirmation before motion,
  camera, and microphone access.
- `examples/play_audio_file.py`: transfer a host WAV file and play it on the robot.
- `examples/capture_photo.py`: capture one JPEG.
- `examples/record_microphone.py`: record five seconds from the robot microphone.

Camera and microphone examples ask for confirmation first. Generated files go to the Git-ignored `artifacts/`
directory. See [examples/README.md](examples/README.md).

## Supported capabilities

| Capability | SDK functions | Return / execution | v1 notes |
|---|---|---|---|
| Connect and close | `WatcheRobot.connect(...)`<br>`robot.close()`<br>`robot.device_info` / `robot.capabilities` | `WatcheRobot` / immediate / read-only properties | Starts LAN Discovery and the WebSocket gateway; one robot per SDK instance |
| Behavior | `robot.behavior.play(id, repeat=1)`<br>`robot.behavior.stop()` | `Job` / immediate | Plays a multi-track Behavior already installed on the robot |
| Animation | `robot.animation.play(id)`<br>`robot.animation.stop()` | `Job` / immediate | Animation resources must already be installed on the robot |
| Point-to-point motion | `robot.motion.move_to(pan_deg=..., tilt_deg=..., duration_ms=...)` | `Job` | `duration_ms` is an integer from `1..65535` milliseconds |
| Real-time motion | `robot.motion.set_target(pan_deg=..., tilt_deg=...)` | immediate | Latest-wins command; does not wait for motion completion |
| Named motion | `robot.motion.play_action(id)`<br>`robot.motion.stop()` | `Job` / immediate | Named actions must already be installed on the robot |
| Installed sound | `robot.audio.play(sound_id)` | `Job` | Sound resources must already be installed on the robot |
| Host audio | `robot.audio.play_file(path)`<br>`robot.audio.play_pcm(data, ...)`<br>`robot.audio.stop()` | `AudioPlayback` / immediate | PCM S16LE, 24 kHz, mono; maximum 4 MB per stream |
| Lights | `robot.lights.set_color(...)`<br>`robot.lights.play_effect(..., period_ms=500)`<br>`robot.lights.off()` | immediate / `Job` / immediate | Colors use `#RRGGBB`; brightness is from `0..1`; `period_ms` is an integer from `0..65535` milliseconds |
| Microphone session | `robot.microphone.open()`<br>`MicrophoneSession.read(timeout=...)`<br>`MicrophoneSession.close()` | `MicrophoneSession` / `AudioFrame` / immediate | Current default is PCM 16 kHz, 16-bit, mono; the bounded queue tracks dropped frames |
| Convenience recording | `robot.microphone.record(duration=...)`<br>`AudioRecording.save(path)` | `AudioRecording` / `Path` | `duration` is in seconds; saves a standard WAV file |
| Camera capture | `robot.camera.capture(...)`<br>`ImageFrame.save(path)` | `ImageFrame` / `Path` | One JPEG frame; continuous video is outside v1 |
| Job lifecycle | `Job.wait(timeout=...)`<br>`Job.cancel()` | `Job` / immediate cancel request | `STARTING → RUNNING → COMPLETED / FAILED / CANCELLED` |

Finite operations return a `Job` or the Job-compatible `AudioPlayback`. An ACK only means that the device accepted
the command; call `Job.wait()` to wait for the device's terminal result.

See [factory resource IDs](docs/resources.md) for safe examples on the current firmware. For pairing, `not_found`,
timeouts, audio validation, and dropped media frames, see [troubleshooting](docs/troubleshooting.md).

## Maintainer hardware checks

The complete light, animation, audio, Behavior, motion, camera, and microphone bench check is intentionally kept out
of the public quickstart:

```bash
python -m pip install -e ".[hardware]"
python tools/hardware_smoke.py --auto-pair-port COM5 --all --non-interactive
```

Serial auto-pairing requires a development firmware built with `CONFIG_WATCHER_DEBUG_CLI_ENABLE`. Production
firmware must keep it disabled. See [docs/hardware-testing.md](docs/hardware-testing.md).

## v1 boundaries

- Behaviors, animations, and `audio.play(sound_id)` must already be installed on the robot.
- Host WAV playback is temporary; arbitrary persistent resource upload is not supported.
- Continuous video, inline Python Behaviors, a public async API, TLS, and remote wake-up are outside v1.
- Closing the SDK, robot app, or connection cancels device jobs, media, and outputs and releases resources.

Protocol details are in [docs/protocol-v1.md](docs/protocol-v1.md).

## Development

```bash
python -m pytest
python -m build
```

Maintainers should follow [docs/releasing.md](docs/releasing.md). Releases use GitHub Actions and PyPI Trusted
Publishing without a long-lived upload token.
