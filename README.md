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
- capture camera images, inspect the active vision backend and model, record
  microphone PCM, and consume face-tracking previews;
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
| Robot capabilities | `app.robot` | High-level domains for behavior, animation, motion, audio, lights, expressions, works, microphone, camera, vision diagnostics, face tracking, and input. |
| Advanced integration | `ApplicationChannels` | Source-aware raw Desktop and Device channels for Applications that own a complete business protocol. |
| Runtime and Daemon | `watcherobot daemon ...` | Pairing, device and Desktop connections, generic frame routing, Application lifecycle, logs, and local control REST API. |
| Robot onboarding | `watcherobot robot ...` | Guided first-time Wi-Fi setup, six-digit-code pairing, and connection status. |
| Application distribution | `watcherobot app ...` | Create, check, publish, submit, browse, download, install, and run reviewed Application snapshots. |
| Advanced Bluetooth provisioning | `watcherobot bluetooth ...` / `BluetoothProvisioner` | Low-level scanning, Wi-Fi credential management, and diagnostics over the existing BLE GATT service. |
| Device maintenance | Daemon maintenance REST API | Desktop-facing firmware, SD-resource, and portable-work maintenance; see [resources](docs/resources.md). |

## Quick start

Before you start, make sure that:

- Your computer has Python 3.10–3.12 installed (3.11 recommended) and Bluetooth available;
- A robot is nearby and powered on (no robot yet? Step 3 still works in offline mode);
- For provisioning, the computer and robot share the same Wi-Fi network.

### 1. Install the SDK

Use a dedicated Conda environment instead of `base`. The SDK supports Python
3.10–3.12; Python 3.11 is recommended:

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
python -m pip install watcherobot
```

The current stable release is
[`watcherobot 0.1.1`](https://pypi.org/project/watcherobot/0.1.1/). For a
reproducible installation, pin it explicitly with
`python -m pip install "watcherobot==0.1.1"`. The unpinned command above is
the normal path for receiving the latest stable release from PyPI.

To test an unpublished PR or commit, create a separate
`watcherobot-source` environment and run `python -m pip install -e .` from
the selected checkout. Install `.[test]` only when contributing to the SDK.

See the [installation guide](docs/installation.md) for both complete paths,
TestPyPI dependency resolution, and command ownership checks. Do not use the
PyPI command to validate unpublished source.

Maintainers should follow the
[SDK release process](docs/releasing.md), including immutable
artifacts, TestPyPI verification, PyPI Trusted Publishing, and the protected
production approval.

Verify the installed command when needed:

```powershell
watcherobot --version
```

### 2. Set up your first robot

`watcherobot robot setup` is an interactive guided command that walks you through
three things: provisioning the robot over Bluetooth (writing the Wi-Fi name and
password), pairing with it (entering a six-digit code), and confirming the final
connection state. Just follow the terminal prompts:

```powershell
watcherobot robot setup
```

What the guide does:

1. The command asks you to turn on computer Bluetooth and open
   **Settings > Wi-Fi** on the robot. Scanning starts only after you confirm
   that the page is open.
2. Results are identified by the stable **Device ID** shown on the robot; when
   several robots are nearby, use **Up/Down** and Enter to select the intended
   Device ID. Older firmware that does not advertise a Device ID is explicitly
   marked as unavailable and shows its Bluetooth ID only as a compatibility
   fallback. The command then reads the Wi-Fi password privately and provisions
   the network.
3. To finish, return to the robot launcher, open the **"Python SDK"** app,
   read the six-digit pairing code at the top of the screen, and enter it in
   the same `robot setup` flow to complete pairing.

Pairing belongs to one-time setup, while `app run`
only starts an Application. Confirm the connection at any time with:

```powershell
watcherobot robot status
```

If the robot is already on the same Wi-Fi, do not reset its network. Open the
**"Python SDK"** app and pair it directly:

```powershell
watcherobot robot pair 123456
```

Replace `123456` with the current code shown by the robot.

### 3. Create and run your first Application

`watcherobot app init` creates an Application project from the built-in
template. Run:

```powershell
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

`app init hello_robot` generates a runnable Hello World application in
`./hello_robot/` with these files:

```text
hello_robot/
├─ app.json     # Application metadata (ID, name, version, ...)
├─ app.py       # Entry point with a complete runnable example
├─ README.md    # Project notes
├─ icon.svg     # Application icon
└─ .gitignore
```

`watcherobot app run` starts this application through the Runtime (Daemon):
the terminal logs a successful greeting, and if a compatible robot is
connected it also plays the `happy` behavior once — that is the robot's way of
saying hello. If no robot is connected, `app run`
explains how to start `watcherobot robot setup`
and continues in offline mode. The Runtime remains the only owner of pairing
and the device connection.

If you hit problems such as a missing device, an unknown pairing code location,
or a not-paired prompt, see [Troubleshooting](docs/troubleshooting.md).

The local control API also exposes `GET /daemon/logs`; see the
[Runtime contract](docs/contracts/runtime-profile-index.md) for product
integration and diagnostics.

### 4. Modify an Application

The `hello_robot/app.py` generated in step 3 contains exactly the code below —
that is what made the robot play `happy`. Edit the file and run
`watcherobot app run` again to see your changes:

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

The initializer generated this same pattern in `hello_robot/app.py`. Always
start applications through `watcherobot app run`, never `python app.py`
directly — an Application must be hosted by the Daemon to receive the device
connection and Desktop channel. Metadata
flags remain available when you are preparing a project for publication:

```powershell
watcherobot app init my_app --id com.example.my_app --author "Example Team"
```

## Common workflows

| Goal | Start here |
| --- | --- |
| Learn from working code | [Application examples](examples/README.md) |
| Build and test an Application end to end | [SDK Application guide](docs/application-marketplace/sdk-application-usage.md) |
| Look up every SDK command | [Complete CLI reference](docs/cli-reference.md) |
| Publish a reviewed Marketplace Application | [Marketplace documentation](docs/application-marketplace/README.md) and [Application distribution reference](docs/application-marketplace/application-cli-reference.md) |
| Provision Wi-Fi over Bluetooth | [Bluetooth provisioning](docs/bluetooth-provisioning.md) |
| Use camera, vision models, microphone, or face tracking | [Device vision diagnostics](docs/vision-diagnostics.md), [face-tracking preview](docs/face-tracking-preview.md), and [microphone audio](docs/microphone-audio.md) |
| Select a device behavior state | [ESP32-S3 v0.3.4 state catalog](docs/device-states/README.md) |
| Work with official resources or Creator works | [Resource and work guide](docs/resources.md) |
| Diagnose pairing, connection, or runtime problems | [Troubleshooting](docs/troubleshooting.md) and [Runtime contract](docs/contracts/runtime-profile-index.md) |

## Useful commands

```powershell
# Runtime lifecycle
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# First-time robot setup and connection
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456

# Application development and distribution
watcherobot app init my_app
cd my_app
watcherobot app run
watcherobot app check .
watcherobot app login
watcherobot app publish .
watcherobot app submit .
watcherobot app marketplace
watcherobot app install <app-id>
watcherobot app list
watcherobot app uninstall <app-id>

# Advanced Bluetooth Wi-Fi diagnostics
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
