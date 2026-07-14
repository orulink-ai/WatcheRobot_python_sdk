# WatcheRobot Python SDK

English | [简体中文](README.zh-CN.md)

`watcherobot` lets a desktop Python program control WatcheRobot Behaviors, motion, animation, audio, lights,
microphone, and camera over the local network. It exposes a small synchronous API while running discovery and the
WebSocket gateway internally.

> v0.1 targets trusted local networks. It uses plain `ws://`, a temporary six-digit pairing code, and one robot per
> SDK instance.

## Before you start

| Requirement | What to check |
|---|---|
| Robot | A WatcheRobot/SenseCAP Watcher-based device with working Wi-Fi |
| Firmware | `V3.1` with **SDK Control App** in the Launcher; the source candidate is currently tracked in [ESP32 PR #96](https://github.com/orulink-ai/WatcheRobot_esp32/pull/96) |
| Network | Computer and robot are on the same LAN; local firewall permits UDP `37021` and TCP `8766` |
| Python | CPython 3.10, 3.11, or 3.12 |

The latest formal ESP32 release does not yet contain SDK Control App, and no supported public `V3.1` binary is
available yet. Contributors can build the candidate with ESP-IDF `v5.2.1`:

```powershell
gh repo clone orulink-ai/WatcheRobot_esp32
Set-Location WatcheRobot_esp32
gh pr checkout 96
& C:\Espressif\frameworks\esp-idf-v5.2.1\export.ps1
Set-Location firmware\s3
idf.py set-target esp32s3
powershell -ExecutionPolicy Bypass -File .\tools\flash-monitor.ps1 COMx -NoWake
```

Replace `COMx` with the robot's serial port. See the candidate
[V3.1 release guide](https://github.com/orulink-ai/WatcheRobot_esp32/blob/codex/esp32-v3.1-release-prep/firmware/s3/docs/V3_1_RELEASE_GUIDE.md)
for its exact build profile and promotion status. Everyone else should wait for a `V3.1` bundle on the
[ESP32 releases](https://github.com/orulink-ai/WatcheRobot_esp32/releases) page instead of flashing an older formal release.

## Install

This source tree prepares the `0.1.0a3` preview. Check the
[TestPyPI project page](https://test.pypi.org/project/watcherobot/) before installing because the immutable build only
appears after the release workflow completes. TestPyPI does not mirror every dependency, so install the runtime
dependency from PyPI first and the SDK itself from TestPyPI without dependency resolution:

```bash
python -m pip install "websockets>=12,<16"
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps watcherobot==0.1.0a3
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
| `0.1.0a3` | `1.0` | `V3.1` with SDK Control App | `>=3.10` (CI: 3.10 / 3.11 / 3.12) | Alpha candidate / TestPyPI after publish |

After connecting, inspect `robot.device_info` and `robot.capabilities`; the negotiated device response is the source
of truth. Firmware older than `V3.1` is not currently covered by the compatibility promise.

## Minimal first connection

1. Connect the computer and robot to the same LAN.
2. Open **SDK Control App** from the robot launcher.
3. Note the temporary six-digit code shown on the robot.
4. Save the following as `hello_robot.py`, then run `python hello_robot.py`:

```python
from watcherobot import WatcheRobot

pairing_code = input("Six-digit pairing code: ").strip()
with WatcheRobot.connect(pairing_code=pairing_code) as robot:
    print("Connected:", robot.device_info)
    print("Capabilities:", robot.capabilities)
    robot.behavior.play("happy", repeat=1).wait(timeout=20)
```

Expected output resembles:

```text
Connected: {'device_id': 'watcher-AF8C', 'firmware_version': 'V3.1', ...}
Capabilities: ('behavior', 'animation', 'motion', ..., 'camera.capture')
```

For a complete exception-handling example covering `ConnectionTimeoutError`, `AuthenticationError`, and structured
`JobFailedError` diagnostics (`error.job_id`, `error.reason`, `error.error_code`), see
[troubleshooting](docs/troubleshooting.md).

## Capability negotiation

Use `robot.supports(...)` before optional calls. Capability names are extensible strings, not a closed enum.

| Negotiated capability | Related API |
|---|---|
| `behavior` | `robot.behavior.*` |
| `animation` | `robot.animation.*` |
| `motion` | `robot.motion.*` |
| `audio` | `robot.audio.play(...)` |
| `audio.stream` | `robot.audio.play_file(...)`, `robot.audio.play_pcm(...)` |
| `light` | `robot.lights.*` |
| `microphone` | `robot.microphone.*` |
| `camera.capture` | `robot.camera.capture(...)` |

```python
if robot.supports("camera.capture"):
    image = robot.camera.capture(timeout=10)
```

## Repository examples

Clone this repository and install it with `python -m pip install -e .` before running these files:

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
| Connect and close | `WatcheRobot.connect(...)`<br>`robot.close()`<br>`robot.device_info` / `robot.capabilities`<br>`robot.supports(capability)` | `WatcheRobot` / immediate / read-only properties | Starts LAN Discovery and the WebSocket gateway; one robot per SDK instance |
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
| Job lifecycle | `Job.wait(timeout=...)`<br>`Job.cancel()`<br>`Job.reason` / `Job.error_code` | `Job` / immediate cancel request | `STARTING → RUNNING → COMPLETED / FAILED / CANCELLED`; terminal errors expose structured diagnostics |

Finite operations return a `Job` or the Job-compatible `AudioPlayback`. An ACK only means that the device accepted
the command; call `Job.wait()` to wait for the device's terminal result.

See [factory resource IDs](docs/resources.md) for safe examples on the current firmware. For pairing, `not_found`,
timeouts, audio validation, and dropped media frames, see [troubleshooting](docs/troubleshooting.md).

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
Publishing without a long-lived upload token. The private serial-assisted bench workflow is documented in
[docs/hardware-testing.md](docs/hardware-testing.md).
