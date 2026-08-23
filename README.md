# WatcheRobot Python SDK

Control your WatcheRobot desktop robot with Python: a few lines of code to make it move, speak, and see.

[![PyPI](https://img.shields.io/pypi/v/watcherobot)](https://pypi.org/project/watcherobot/)
[![Python](https://img.shields.io/pypi/pyversions/watcherobot)](https://pypi.org/project/watcherobot/)

> 🌐 English | [中文文档](README.zh-CN.md)

## 🚀 Quick start in 5 minutes

```powershell
# 1. Install (Python 3.11 recommended, in a dedicated environment)
conda create -n watcherobot python=3.11 -y
conda activate watcherobot
python -m pip install watcherobot

# 2. Pair your robot for the first time (guided, just follow the prompts)
watcherobot robot setup

# 3. Create and run your first application
watcherobot app init hello_robot
cd hello_robot
watcherobot app run
```

> 💡 **Don't have Conda?** Two options:
>
> - **Simplest path** — skip Conda and install into your system Python (3.10–3.12):
>   ```powershell
>   python -m pip install --upgrade pip
>   python -m pip install watcherobot
>   ```
> - **Recommended long-term** — [install Miniconda](https://docs.anaconda.com/miniconda/install/)
>   first, then run the commands above. A dedicated environment avoids dependency
>   conflicts with other Python projects on your machine.
>
> Both paths end with the same `watcherobot` command; you can switch later at any time.

Once that works, make the robot happy:

```python
import asyncio
from watcherobot.application import ApplicationContext

async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        job = await asyncio.to_thread(app.robot.behavior.play, "happy")
        await asyncio.to_thread(job.wait, 20.0)

asyncio.run(main())
```

No robot at hand? `app run` enters offline mode and explains what to do next.

## 🧭 What do you want to do?

| Your goal | Go here |
| --- | --- |
| Make the robot act / speak / light up | [Quick start](#-quick-start-in-5-minutes) and the [SDK Application guide](docs/application-marketplace/sdk-application-usage.md) |
| Read the source, understand how it works | [How it works (Runtime/Daemon)](#️-how-it-works-runtimedaemon) |
| Use the camera / microphone / face tracking | [Vision diagnostics](docs/vision-diagnostics.md), [face-tracking preview](docs/face-tracking-preview.md), [microphone audio](docs/microphone-audio.md) |
| Provision Wi-Fi over Bluetooth | [Bluetooth provisioning](docs/bluetooth-provisioning.md) |
| Publish an app to the Marketplace | [Marketplace documentation](docs/application-marketplace/README.md) |
| Pairing or connection problems | [Troubleshooting](docs/troubleshooting.md) |
| Look up every CLI command | [Complete CLI reference](docs/cli-reference.md) |
| Learn from working code | [Application examples](examples/README.md) |

## ⚙️ How it works? (Runtime/Daemon)

This package has two complementary roles:

- **Application SDK** — the public Python API for building robot experiences.
- **Runtime/Daemon** — the single local runtime that pairs with the robot,
  owns its device connection, and manages Application processes.

Your Application focuses on product behavior; the Runtime handles pairing,
connections, lifecycle, logs, and transport. Watcher Desktop uses this same
Runtime/Daemon implementation—it does not embed another Daemon.

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
watcherobot daemon start / status / stop

# First-time robot setup and connection
watcherobot robot setup
watcherobot robot status
watcherobot robot pair 123456      # replace with the code shown on the robot

# Application development and distribution
watcherobot app init my_app && cd my_app && watcherobot app run
watcherobot app check .            # validate before publishing
watcherobot app publish .          # publish to the Marketplace after login
```

See the [complete CLI reference](docs/cli-reference.md) for every command.

## ✅ Requirements and support

- Python 3.10–3.12 (3.11 recommended)
- Windows or macOS for Bluetooth Wi-Fi provisioning
- A WatcheRobot device for pairing and hardware features

Current stable release: [`watcherobot 0.1.1`](https://pypi.org/project/watcherobot/0.1.1/).
For maintainers, see the [release process](docs/releasing.md).
Licensed under [Apache-2.0](LICENSE).
