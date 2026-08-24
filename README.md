# WatcheRobot Python SDK

Control your WatcheRobot desktop robot with Python: a few lines of code to make it move, speak, and see.

[![PyPI](https://img.shields.io/pypi/v/watcherobot)](https://pypi.org/project/watcherobot/)
[![Python](https://img.shields.io/pypi/pyversions/watcherobot)](https://pypi.org/project/watcherobot/)

> 🌐 English | [中文文档](README.zh-CN.md)

<a id="quick-start"></a>

## 🚀 Quick start

Before you start:

- install Python 3.10–3.12 (3.11 recommended);
- use Windows or macOS with Bluetooth for first-time robot setup;
- keep the robot nearby and powered on for hardware steps. Without a robot,
  you can still create and run the sample Application in offline mode;
- keep the computer and robot on the same Wi-Fi network when pairing.

### 1. Install the SDK

```powershell
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install --upgrade pip
python -m pip install watcherobot
```

Don't want to use Conda? Create an isolated `venv` instead of installing into
the system Python:

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install watcherobot
```

```sh
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install watcherobot
```

Confirm that the SDK is installed and that the command comes from the active
environment:

```powershell
# Windows PowerShell
Get-Command watcherobot
watcherobot --version
python -m pip show watcherobot
```

```sh
# macOS / Linux
command -v watcherobot
watcherobot --version
python -m pip show watcherobot
```

For PEP 668, PATH, `python3`, source-checkout, and TestPyPI troubleshooting,
see the [installation guide](docs/installation.md).

### 2. Set up your first robot

`watcherobot robot setup` is an interactive guide that provisions Wi-Fi,
pairs the robot with the Runtime, and confirms the final connection:

```powershell
watcherobot robot setup
```

The guide asks you to:

1. enable Bluetooth and open **Settings > Wi-Fi** on the robot;
2. select the matching **Device ID** with **Up/Down**, then enter the Wi-Fi
   credentials privately;
3. open the **"Python SDK"** app on the robot and enter its six-digit pairing
   code in the same setup flow.

If the robot is already on Wi-Fi, do not reset the network. Open the
**"Python SDK"** app and pair with its current code:

```powershell
watcherobot robot pair 123456
watcherobot robot status
```

Replace `123456` with the code currently shown by the robot. Older firmware
that does not advertise a Device ID is clearly marked and falls back to its
Bluetooth ID for compatibility.

### 3. Create and run the first Application

Run each command separately so the flow works in Windows PowerShell 5.1,
PowerShell 7, and macOS/Linux shells:

```powershell
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

The initializer creates:

```text
hello_robot/
├─ app.json     # Application identity, version, and dependencies
├─ app.py       # managed Application entry point
├─ README.md    # generated project instructions
├─ icon.svg     # Application icon
└─ .gitignore
```

`watcherobot app run` starts the project through the Runtime/Daemon. The
terminal prints the Application greeting; when a compatible robot is
connected, it also plays the `happy` behavior once. Without a robot, the
Application continues in offline mode and explains how to connect one.

Always start an Application with `watcherobot app run`, never with
`python app.py`: the Daemon must inject the Device and Desktop channels.
For setup or connection failures, see [troubleshooting](docs/troubleshooting.md).

### 4. Understand and modify the sample

The generated `hello_robot/app.py` contains the code below. Edit that file,
then run `watcherobot app run` again from the `hello_robot` directory:

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

The context exposes `app.robot` for robot capabilities, `app.desktop` for
Watcher Desktop business messages, and `app.logger` for Application logs.
When preparing a real project, generate its stable metadata explicitly:

```powershell
watcherobot app init my_app --id com.example.my_app --author "Example Team"
```

## 🧭 What do you want to do?

| Your goal | Go here |
| --- | --- |
| Make the robot act / speak / light up | [Quick start](#quick-start) and the [SDK Application guide](docs/application-marketplace/sdk-application-usage.md) |
| Read the source and understand how it works | [Source and repository boundaries](#source-boundaries) and [How it works](#runtime-daemon) |
| Use the camera / microphone / face tracking | [Vision diagnostics](docs/vision-diagnostics.md), [face-tracking preview](docs/face-tracking-preview.md), [microphone audio](docs/microphone-audio.md) |
| Provision Wi-Fi over Bluetooth | [Bluetooth provisioning](docs/bluetooth-provisioning.md) |
| Publish an app to the Marketplace | [Marketplace documentation](docs/application-marketplace/README.md) |
| Pairing or connection problems | [Troubleshooting](docs/troubleshooting.md) |
| Look up every CLI command | [Complete CLI reference](docs/cli-reference.md) |
| Learn from working code | [Application examples](examples/README.md) |

<a id="runtime-daemon"></a>

## ⚙️ How it works (Runtime/Daemon)

This package has two complementary roles:

- **Application SDK** — the public Python API for building robot experiences.
- **Runtime/Daemon** — the single local runtime that pairs with the robot,
  owns its device connection, and manages Application processes.

Your Application focuses on product behavior; the Runtime handles pairing,
connections, lifecycle, logs, and transport. Watcher Desktop does not embed
another Daemon; desktop uses this same Runtime/Daemon implementation.

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

<a id="source-boundaries"></a>

## 🧩 Source and repository boundaries

- `src/watcherobot/application/` contains the managed Application API and
  channel contracts.
- `src/watcherobot/runtime/daemon/` is the only Runtime/Daemon source. Watcher
  Desktop installs and starts this implementation instead of maintaining a
  second Daemon.
- `src/watcherobot/vision.py` contains the typed edge-vision and face-tracking
  Application APIs.
- The separate Desktop repository owns desktop UI and packaging. The official
  default Application lives in `WatcheRobot_server`; the SDK does not own its
  ASR, LLM, or TTS product logic.
- In the official Workspace, `yarn desktop:dev` binds the current SDK checkout
  into the Workspace-managed environment. See the
  [installation guide](docs/installation.md) for source-development details.

## 🛠️ What can I build?

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

## 📦 Common workflows

| Goal | Start here |
| --- | --- |
| Build and test an Application end to end | [SDK Application guide](docs/application-marketplace/sdk-application-usage.md) |
| Publish a reviewed Marketplace Application | [Marketplace documentation](docs/application-marketplace/README.md) and [distribution reference](docs/application-marketplace/application-cli-reference.md) |
| Select a device behavior state | [ESP32-S3 v0.3.4 state catalog](docs/device-states/README.md) |
| Work with official resources or Creator works | [Resource and work guide](docs/resources.md) |
| Diagnose pairing, connection, or runtime problems | [Troubleshooting](docs/troubleshooting.md) and [Runtime contract](docs/contracts/runtime-profile-index.md) |
| Integrate with the official Workspace from source | `yarn desktop:dev` (see [Workspace notes](docs/installation.md)) |

## ⌨️ Command cheat sheet

```powershell
# Runtime lifecycle
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

# First-time robot setup and connection
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456      # replace with the code shown on the robot

# Application development and distribution
watcherobot app init my_app
cd my_app
watcherobot app run
watcherobot app login
watcherobot app check .            # validate before publishing
watcherobot app publish .          # upload an immutable source snapshot
watcherobot app submit .           # submit that snapshot for Marketplace review
watcherobot app install <app-id>   # install from the Marketplace
watcherobot app list               # list installed applications
watcherobot app uninstall <app-id> # uninstall
```

For normal troubleshooting, start with `watcherobot daemon status`. Product
integrations can read `GET /daemon/logs` from the discovered local control URL;
see the [Runtime contract](docs/contracts/runtime-profile-index.md) for endpoint
discovery and response details. See the
[complete CLI reference](docs/cli-reference.md) for every command.

## ✅ Requirements and support

- Python 3.10–3.12 (3.11 recommended)
- Windows or macOS for Bluetooth Wi-Fi provisioning
- A WatcheRobot device for pairing and hardware features

See the PyPI badge above for the current stable release.
For maintainers, see the [release process](docs/releasing.md).
Licensed under [Apache-2.0](LICENSE).
