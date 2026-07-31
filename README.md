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

## Display text

When the device advertises the `display.text` capability, an Application can
show text over the current animation and clear it without stopping the
animation:

```python
app.robot.display.show_text("Hello\nWatcheRobot")
app.robot.display.clear()
```

Text is limited to 30 Unicode characters and 90 UTF-8 bytes. Newlines are
supported; other control characters are rejected.

## Display images and live frames

Devices advertising `display.image` accept a baseline JPEG from bytes, an
`ImageFrame`, or a file path:

```python
app.robot.display.show_image("status.jpg")
```

Devices advertising `display.stream` can display a finite or live iterable of
JPEG frames. The call is synchronous, so async Applications should run it in a
worker thread:

```python
sent = await asyncio.to_thread(
    app.robot.display.stream,
    jpeg_frames(),
    fps=10,
)
```

JPEGs are validated on the host before transmission. They must be baseline
(not progressive), no larger than 412x412 pixels, and at most 512 KiB each.
The default requested rate is 10 FPS and the SDK caps requests at 15 FPS.
Actual display rate depends on JPEG size, Wi-Fi conditions, and decode time.
The device keeps only the newest pending frame, so overload drops stale frames
instead of building latency or an unbounded queue.

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
