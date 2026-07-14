# WatcheRobot Python SDK

English | [简体中文](README.zh-CN.md)

`watcherobot` is the synchronous desktop/LAN SDK for WatcheRobot. The computer starts a UDP discovery service and
WebSocket gateway; the robot's **SDK Control App** discovers it and connects back.

> v0.1 targets trusted local networks. It uses plain `ws://`, a temporary six-digit pairing code, and one robot per
> SDK instance.

## Install

For development or evaluation from this repository:

```bash
python -m pip install -e .
```

After a PyPI release, install with:

```bash
python -m pip install watcherobot
```

Python 3.10 or newer is required.

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

## API model

- `robot.behavior.play(...)` plays an installed multi-track Behavior.
- `robot.animation`, `robot.motion`, `robot.audio`, and `robot.lights` provide direct domain control.
- `robot.audio.play(sound_id)` plays an installed resource; `robot.audio.play_file(path)` transfers a host WAV.
- Finite operations return a `Job`; `Job.wait()` observes the device terminal event, not merely the ACK.
- `motion.set_target(...)` is a latest-wins real-time command and does not return a Job.
- `robot.microphone.open()` exposes PCM S16LE, 16 kHz, mono frames and dropped-frame statistics.
- `robot.microphone.record(duration=5)` returns a saveable `AudioRecording` directly.
- `robot.camera.capture()` returns one JPEG `ImageFrame`.
- `AudioRecording.save(path)` and `ImageFrame.save(path)` create parent directories and save standard files.

In v1, `play_file()` accepts PCM S16LE, 24 kHz, mono WAV files up to 4 MB.

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
