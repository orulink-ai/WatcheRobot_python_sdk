# WatcheRobot Python SDK

The SDK is the single owner of the WatcheRobot Runtime/Daemon and the public
Application API. Programs built with this package are managed Applications:
they can run from the CLI without the desktop and can also be installed and
started by the desktop.

## Install and run

```powershell
python -m pip install -e ".[test]"
watcherobot app run .\examples\hello_robot
```

`watcherobot app run` starts or reuses the current user's one Runtime. The
Runtime owns pairing, device and desktop connections, Application processes,
logs, and routing. When the Application exits, the Runtime remains alive.

Useful commands:

```powershell
watcherobot daemon start
watcherobot daemon status
watcherobot daemon stop

watcherobot app package .\examples\hello_robot .\dist\hello_robot.wapp
watcherobot app install .\dist\hello_robot.wapp
watcherobot app list
watcherobot app select example.hello_robot --version 1.0.0
watcherobot app start
watcherobot app stop
watcherobot app uninstall example.hello_robot --version 1.0.0
```

For larger Applications, add a `.wappignore` file using glob patterns such as
`tests/`, `.venv*/`, and `*.tmp`; the ignore file itself is not packaged.

## Application API

Every Application has `app.json` and a fixed `app.py` entrypoint:

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("capabilities=%s", app.robot.capabilities)
        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

- `app.robot` retains the domain, Job, input, and media APIs and can only use
  the Daemon-authorized device channel.
- `app.desktop` sends and receives optional desktop frames.
- `app.logger` writes process logs captured and persisted by the Runtime.

Applications that already own a complete business protocol stack may use the
advanced `ApplicationChannels` API to receive source-aware raw desktop/device
frames. Normal SDK Applications should use `ApplicationContext`.

Applications never open their own Discovery socket or device WebSocket and do
not receive pairing credentials.

See [examples](examples/README.md), [Runtime contract](docs/contracts/runtime-profile-index.md),
and [troubleshooting](docs/troubleshooting.md).
