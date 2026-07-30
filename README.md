# WatcheRobot Python SDK

The SDK is the single owner of the WatcheRobot Runtime/Daemon and the public
Application API. Programs built with this package are managed Applications:
they can run without the desktop through the CLI and Runtime control API. The
desktop uses this same Runtime/Daemon implementation to launch the bundled
default Application and SDK-built Applications.

## Install and run

Install this checkout for development:

```powershell
python -m pip install -e ".[test]"
```

`watcherobot app run` starts or reuses the current user's one Runtime. The
Runtime owns pairing, device and desktop connections, Application processes,
logs, and routing. When the Application exits, the Runtime remains alive.
Current-session Daemon logs are available from `GET /daemon/logs`; this source
works whether the Runtime was launched by the desktop or independently.

For a fresh standalone session, start the Runtime, replace `123456` with the
six-digit code shown by the device, pair through the local control API, and
then run the Application:

```powershell
$runtime = watcherobot daemon start | ConvertFrom-Json
$pairBody = '{"pairing_code":"123456","target_mode":"desktop_link"}'
Invoke-RestMethod `
  -Method Post `
  -Uri "$($runtime.control_url)/daemon/devices/pair" `
  -ContentType "application/json" `
  -Body $pairBody
do {
  $device = (Invoke-RestMethod `
    -Uri "$($runtime.control_url)/daemon/devices").device
  if ($device.state -eq "idle") {
    throw "Pairing failed: $($device.last_error)"
  }
  if ($device.state -ne "connected") {
    Start-Sleep -Milliseconds 250
  }
} while ($device.state -ne "connected")
watcherobot app run .\examples\hello_robot
```

If the current Runtime already owns a connected device session, skip the pair
request and run the Application directly.

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

## Microphone PCM

The device microphone uplink is Opus (`16 kHz`, mono, one packet per WSPK
frame). Normal Applications receive decoded `pcm_s16le` through the high-level
SDK API:

```python
with app.robot.microphone.open_pcm() as microphone:
    frame = microphone.read(timeout=1.0)  # frame.data is 16 kHz mono PCM

recording = app.robot.microphone.record_pcm(duration=3.0)
recording.save("microphone.wav")
```

`open()` and `record()` are PCM aliases for compatibility. The Runtime/Daemon
only owns the device connection and routes the WSPK frame; it does not decode
or rewrite an Opus payload. Decoding is performed in this SDK's Application
media layer, so a desktop bundle must ship the complete shared `watcherobot`
environment, while the desktop itself continues to control only the Daemon.

Applications that intentionally implement their own media protocol can use
the advanced `ApplicationChannels` Device channel and consume raw WSPK frames.

See [examples](examples/README.md), [Runtime contract](docs/contracts/runtime-profile-index.md),
the [microphone contract](docs/microphone-audio.md), and [troubleshooting](docs/troubleshooting.md).
