# WatcheRobot Python SDK

`watcherobot` is the synchronous desktop/LAN SDK for WatcheRobot. It starts a small UDP discovery service and
WebSocket gateway on your computer; the robot's **Python SDK** app discovers it and connects back to it.

> v0.1 is intended for trusted local networks. It uses plain `ws://`, one temporary pairing session, and one robot
> per SDK instance.

## Install

```bash
pip install watcherobot
```

Python 3.10 or newer is required.

## Quick start

1. Connect the computer and WatcheRobot to the same LAN.
2. Open **Python SDK** from the robot launcher. A new six-digit code appears.
3. Run:

```python
from watcherobot import WatcheRobot

with WatcheRobot.connect(pairing_code="123456") as robot:
    job = robot.behavior.play("happy", repeat=1)
    job.wait(timeout=5)

    robot.motion.move_to(pan_deg=110, tilt_deg=120, duration=0.5).wait(timeout=2)
    robot.motion.set_target(pan_deg=105)
    robot.animation.play("smile")
    robot.audio.play("confirm")
    robot.lights.set_color("#4DA3FF", brightness=0.7)

    with robot.microphone.open() as microphone:
        frame = microphone.read(timeout=1)
        print(frame.data, microphone.dropped_frames)

    image = robot.camera.capture(timeout=5)
    with open("capture.jpg", "wb") as output:
        output.write(image.data)
```

The default discovery and WebSocket ports are `37021/UDP` and `8766/TCP`. They can be changed when ports conflict:

```python
robot = WatcheRobot.connect(
    pairing_code="123456",
    discovery_port=37022,
    websocket_port=8767,
)
```

The firmware must use the same discovery port. The WebSocket port is announced dynamically during discovery.

## API model

- `robot.behavior.play(...)`: plays a named, installed device-side multi-track Behavior.
- `robot.animation`, `robot.motion`, `robot.audio`, `robot.lights`: direct domain control.
- `move_to`, `play_action`, `play`, and finite light effects return a `Job`.
- `set_target` is a latest-wins real-time command and does not return a `Job`.
- `Job.wait()` observes the device terminal event; an ACK alone does not mean playback completed.
- `move_to` completes from the matching STM32 execution event. It confirms execution-timeline completion, not
  physical position-feedback convergence.
- `robot.microphone.open()` exposes PCM S16LE, 16 kHz, mono frames. Its bounded queue drops the oldest frame when
  the consumer is slow and increments `dropped_frames`.
- `robot.camera.capture()` returns one JPEG `ImageFrame`; continuous video is outside v1.

Direct control cancels a running Behavior first. A new direct command replaces an older command in the same domain,
while different direct domains may run together.

## v1 boundaries

- Behaviors and media resources must already be installed on the robot.
- No resource upload, inline Behavior timeline, continuous video, public async API, TLS, or remote wake-up.
- Closing the SDK, closing the robot app, or losing the connection causes device-side cancellation and safe cleanup.

Protocol details are in [docs/protocol-v1.md](docs/protocol-v1.md).

## Development

For unattended bench smoke tests, build the firmware with `CONFIG_WATCHER_DEBUG_CLI_ENABLE=y`, connect its debug
UART, and run:

```bash
pip install -e ".[hardware]"
python examples/hardware_smoke.py --auto-pair-port COM5 --all --websocket-port 18766
```

The debug firmware opens `sdk.control.app` over the serial CLI and prints the temporary code for the test runner.
Production firmware keeps this option disabled and never writes pairing codes to UART.

```bash
python -m pytest
python -m build
```
