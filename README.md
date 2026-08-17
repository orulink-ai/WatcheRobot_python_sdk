# WatcheRobot Python SDK

Build, run, and distribute applications for WatcheRobot.

This package has two complementary roles:

- **Application SDK** — the public Python API for building robot experiences.
- **Runtime/Daemon** — the single local runtime that pairs with the robot,
  owns its device connection, and manages Application processes.

Your Application focuses on product behavior; the Runtime handles pairing,
connections, lifecycle, logs, and transport. Watcher Desktop uses this same
Runtime/Daemon implementation—it does not embed another Daemon. In other
words, desktop uses this same Runtime/Daemon implementation rather than a
separate copy.

## What can I build?

Use the SDK to create a managed Application that can:

- control behaviors, animations, motion, lights, expressions, works, and audio;
- capture camera images, record microphone PCM, and consume face-tracking
  previews;
- receive touch and roller input events;
- exchange optional business messages with Watcher Desktop;
- be launched locally with the CLI or installed and launched by Watcher
  Desktop after Marketplace review.

The SDK also provides Bluetooth Wi-Fi provisioning, Application project
scaffolding and validation, Marketplace publishing tools, and Runtime control
APIs for products that integrate with WatcheRobot.

## Architecture at a glance

```text
Your Application
  └─ ApplicationContext / ApplicationChannels
       └─ WatcheRobot Runtime (Daemon)
            ├─ pairing, device connection, logs, process lifecycle
            ├─ Desktop channel ─────────────── Watcher Desktop
            └─ Device channel ──────────────── WatcheRobot device
```

An Application never opens its own discovery socket or device WebSocket, and
never receives pairing credentials. When an Application is running, Desktop
and device business frames pass through that Application. Without one, the
Runtime transparently forwards frames between Desktop and device.

## Running in the official Workspace

Use `yarn desktop:dev` at the `WatcheRobot-Workspace` root for full source
integration. The root command installs the current SDK checkout into a
workspace-managed virtual environment, treats it as the only Daemon source,
and verifies runtime imports before Desktop starts. It does not consume a
Conda interpreter, system SDK, or packaged Runtime inherited from the caller.

This repository owns the Application API, Daemon, Runtime control plane, and
distribution tooling. It does not own Desktop UI or packaging orchestration,
and it does not implement the official default Application's ASR/LLM/TTS
business logic.

## Modules

| Area | Main entry points | What it is for |
| --- | --- | --- |
| Application development | `watcherobot.application.ApplicationContext` | The normal starting point for a managed Application. Provides `app.robot`, `app.desktop`, and `app.logger`. |
| Robot capabilities | `app.robot` | High-level domains for behavior, animation, motion, audio, lights, expressions, works, microphone, camera, face tracking, and input. |
| Advanced integration | `ApplicationChannels` | Source-aware raw Desktop and Device channels for Applications that own a complete business protocol. |
| Runtime and Daemon | `watcherobot daemon ...` | Pairing, device and Desktop connections, generic frame routing, Application lifecycle, logs, and local control REST API. |
| Application distribution | `watcherobot app ...` | Create, check, publish, submit, browse, download, install, and run reviewed Application snapshots. |
| Bluetooth provisioning | `watcherobot bluetooth ...` / `BluetoothProvisioner` | Scan, provision Wi-Fi credentials, inspect status, and clear Wi-Fi credentials over the existing BLE GATT service. |
| Device maintenance | Daemon maintenance REST API | Desktop-facing firmware, SD-resource, and portable-work maintenance; see [resources](docs/resources.md). |

## Quick start

### 1. Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

### 2. Run a complete example

Every example is a managed Application with an `app.json` manifest and fixed
`app.py` entrypoint. Start it through the Runtime, not by executing `app.py`
directly:

```powershell
watcherobot app run .\examples\hello_robot
```

For a fresh standalone session, start the Runtime, pair the device using its
six-digit code, and then run the example:

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
$pairBody = '{"pairing_code":"123456","target_mode":"python_sdk"}'
Invoke-RestMethod `
  -Method Post `
  -Uri "$($runtime.control_url)/daemon/devices/pair" `
  -ContentType "application/json" `
  -Body $pairBody
watcherobot app run .\examples\hello_robot
```

Replace `123456` with the code shown by the device. If the Runtime already has
a connected device session, skip the pairing request.

The local control API also exposes `GET /daemon/logs` for inspecting Runtime
logs. See the [Runtime contract](docs/contracts/runtime-profile-index.md) for
the full REST boundary.

### 3. Write an Application

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

Create a publish-ready project instead of starting from scratch:

```powershell
watcherobot app init .\my_app
watcherobot app check .\my_app
watcherobot app run .\my_app
```

## Common workflows

| Goal | Start here |
| --- | --- |
| Learn from working code | [Application examples](examples/README.md) |
| Build and test an Application end to end | [SDK Application guide](docs/application-marketplace/sdk-application-usage.md) |
| Look up every SDK command | [Complete CLI reference](docs/cli-reference.md) |
| Publish a reviewed Marketplace Application | [Marketplace documentation](docs/application-marketplace/README.md) and [Application distribution reference](docs/application-marketplace/application-cli-reference.md) |
| Provision Wi-Fi over Bluetooth | [Bluetooth provisioning](docs/bluetooth-provisioning.md) |
| Use camera, microphone, or face tracking | [Face-tracking preview](docs/face-tracking-preview.md) and [microphone audio](docs/microphone-audio.md) |
| Select a device behavior state | [ESP32-S3 v0.3.4 state catalog](docs/device-states/README.md) |
| Work with official resources or Creator works | [Resource and work guide](docs/resources.md) |
| Diagnose pairing, connection, or runtime problems | [Troubleshooting](docs/troubleshooting.md) and [Runtime contract](docs/contracts/runtime-profile-index.md) |

## Useful commands

```powershell
# Runtime lifecycle
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# Application development and distribution
watcherobot app init .\my_app
watcherobot app check .\my_app
watcherobot app run .\my_app
watcherobot app login
watcherobot app publish .\my_app
watcherobot app submit .\my_app
watcherobot app marketplace
watcherobot app install <app-id>
watcherobot app list
watcherobot app uninstall <app-id>

# Bluetooth Wi-Fi provisioning
watcherobot bluetooth scan
watcherobot bluetooth provision --device <id> --ssid MyWiFi
watcherobot bluetooth status --device <id>
```

See the [complete CLI reference](docs/cli-reference.md) for every
`watcherobot` command, its parameters, side effects, and Runtime boundary.

## Requirements and support

- Python 3.10–3.12
- Windows or macOS for Bluetooth Wi-Fi provisioning
- A WatcheRobot device for pairing and hardware features

The package is licensed under [Apache-2.0](LICENSE).
