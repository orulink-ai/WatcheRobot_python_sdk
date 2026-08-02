# WatcheRobot Python SDK

The SDK is the single owner of the WatcheRobot Runtime/Daemon and the public
Application API. Programs built with this package are managed Applications:
they can run without the desktop through the CLI and Runtime control API. The
desktop uses this same Runtime/Daemon implementation to launch the bundled
default Application and SDK-built Applications.

## Install and run

Use an isolated virtual environment and install this checkout in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Examples are Runtime-managed Applications. Start them with
`watcherobot app run .\examples\<example-directory>` instead of running
`app.py` directly. The Runtime injects the required `WATCHER_APP_*`
environment variables when it launches an Application.

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

## Bluetooth Wi-Fi provisioning

Python 3.10–3.12 on Windows and macOS can provision the existing
`ESP_ROBOT` GATT service directly:

```powershell
watcherobot bluetooth scan
watcherobot bluetooth provision --device <id> --ssid MyWiFi
watcherobot bluetooth status --device <id>
watcherobot bluetooth clear --device <id>
```

The provision command prompts for the password without accepting a
`--password` argument. Its `credentials_saved` result means only that the
firmware acknowledged storage of the credentials; it does not prove that the
device connected to the network. The SDK makes a bounded attempt to stop
notifications and disconnect BLE before returning. Cleanup failure does not
replace an acknowledged `credentials_saved` result, so that result does not
guarantee BLE disconnected or the firmware resumed its Wi-Fi attempt.

The same flow is available through the asynchronous
`BluetoothProvisioner` API. See
[Bluetooth provisioning](docs/bluetooth-provisioning.md) for API examples,
timeouts, platform identifiers, protocol limits, and the current GATT
security constraints.

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

## Face-tracking preview

The managed SDK can consume the live JPEG stream and the face-tracking
telemetry as one typed, sequence-matched frame:

```python
preview = await asyncio.to_thread(
    app.robot.face_tracking.open_preview,
    width=416,
    height=416,
)
async with preview:
    async for frame in preview:
        app.logger.info(
            "frame=%d faces=%d inference_ms=%s",
            frame.sequence,
            len(frame.faces),
            frame.telemetry.inference_ms,
        )
```

The queue is bounded and keeps the newest complete frame by default, so a slow
consumer does not turn temporary congestion into seconds of stale video. The
Runtime owns the device and UDP/WebSocket routing; Applications only use the
typed API. See [face-tracking preview](docs/face-tracking-preview.md) and the
[example Application](examples/face_tracking_preview).

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

`open()` retains the raw Opus-packet stream for Applications that need to
process packets themselves. Raw Opus does not provide byte-derived PCM
duration or WAV output, so `record()` now raises a migration error instead of
creating a misleading recording; use `open()` for raw packets or
`record_pcm()` for a PCM recording. The Runtime/Daemon only owns the device
connection and routes the WSPK frame; it does not decode or rewrite an Opus
payload. Decoding is performed in this SDK's Application media layer, so a
desktop bundle must ship the complete shared `watcherobot` environment, while
the desktop itself continues to control only the Daemon.

Applications that intentionally implement their own media protocol can use
the advanced `ApplicationChannels` Device channel and consume raw WSPK frames.

See [examples](examples/README.md), [Runtime contract](docs/contracts/runtime-profile-index.md),
the [microphone contract](docs/microphone-audio.md), and [troubleshooting](docs/troubleshooting.md).
