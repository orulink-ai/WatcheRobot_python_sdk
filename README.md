# WatcheRobot Python SDK

The SDK is the single owner of the WatcheRobot Runtime/Daemon and the public
Application API. Programs built with this package are managed Applications:
they can run without the desktop through the CLI and Runtime control API. The
desktop uses this same Runtime/Daemon implementation. The Daemon can start
without a selected Application; Desktop may then install, select, and start an
SDK-built Application through the Catalog and control APIs.

The Application Marketplace SDK contracts, versions, error codes, and
implementation evidence are indexed in
[`docs/application-marketplace/README.md`](docs/application-marketplace/README.md).
To test the current SDK Application and publishing flow step by step, use the
[English SDK Application guide](docs/application-marketplace/sdk-application-usage.md).
A [Chinese version](docs/application-marketplace/sdk-application-usage.zh-CN.md)
is also available.

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

watcherobot app init .\my_app
watcherobot app check .\my_app
watcherobot app login
watcherobot app login --status
watcherobot app publish .\my_app
watcherobot app submit .\my_app
watcherobot app marketplace
watcherobot app marketplace --details
watcherobot app download --space-id <user>/WatcherRobot-<app_id> --commit <40-char-sha> --target .\staging\app
watcherobot app install --space-id <user>/WatcherRobot-<app_id> --commit <40-char-sha> --store-root <app-store-dir> --runtime-root <locked-app-runtime-dir>
watcherobot app list --store-root <app-store-dir>
watcherobot app run-installed --store-root <app-store-dir> --app-id <app_id>
watcherobot app uninstall --store-root <app-store-dir> --app-id <app_id>
watcherobot app logout
watcherobot app start
watcherobot app stop
```

`app run` accepts a source directory for local development. The SDK owns
download, installation, inventory, and removal. It installs every reviewed
immutable snapshot below one SDK App Store root, creates one isolated `.venv`
per App, and records the installed commit. `--runtime-root` must point to the
locked Application Runtime bundled with Desktop. Selecting, starting, and
stopping the current Application remain Daemon management actions.

`watcherobot app run-installed --store-root ... --app-id ...` is the SDK
developer-test path for an App installed into a custom store. It reads the SDK
installation record, starts or reuses an isolated Daemon rooted at that store,
and launches the App with its own `.venv`. It uses ephemeral ports and never
reuses or modifies the Desktop Daemon. Desktop production installs into its own
managed store and remains responsible for selection and start/stop in its UI.

`watcherobot app init <new-directory>` creates a complete publish-ready project
without starting the Daemon or overwriting an existing path. In a terminal it
prompts for the Application ID, display name, author, and description; scripts
can provide `--id`, `--name`, `--author`, and `--description`. The generated
`app.json` uses version `0.1.0`, derives a bounded compatibility range from the
installed SDK, and includes a default `icon.svg`.

`watcherobot app check <directory>` validates the canonical `app.json`, fixed
`app.py`, SDK compatibility, standard Python dependency requirements, icon
path, and the source set that can later be published. It does not start the
Daemon. Desktop callers use `--jsonl`; stdout then contains only one
`progress`, `result`, or `error` JSON object per line.

`watcherobot app login` uses the Watcher Desktop public OAuth Device Flow. It
prints the Hugging Face authorization URL and user code, then saves the token
only in Watcher's operating-system credential entry after the identity has
been verified. `--force` starts a new authorization even when the saved token
is valid. `app login --status` verifies the saved identity, and `app logout`
deletes only Watcher's credential. These commands do not start the Daemon and
also support `--jsonl` for Desktop callers.

`watcherobot app publish <directory>` first performs the same local checks,
then creates or updates the public
`<hf_username>/WatcherRobot-<app_id>` Hugging Face Space with the exact source
snapshot. The Space is a source repository only: publishing does not generate
a web page. A successful result contains the Space and fixed source commit; it
does not read or modify the official Catalog. The command does not start the
Daemon and supports `--jsonl` for Desktop callers.

`watcherobot app submit <directory>` requires non-empty `description` and
`author`, verifies the already-published fixed snapshot, and opens or reuses
the official Catalog pull request without uploading source. `icon` is optional:
when present it is verified at the fixed revision; when absent, presentation
clients use the default WatcherRobot Application icon. Add
`--commit <40-char-sha>` to submit an exact published revision; otherwise the
current Space HEAD is used. The PR renders the reviewed manifest and fixed
source link, plus the fixed-revision icon when one was provided.

`watcherobot app marketplace` publicly loads and validates the official
Application list and each reviewed `app.json` at its fixed commit. The result
contains the observed Dataset commit and structured Applications, including
SDK compatibility and fixed source links. It needs no Hugging Face login,
does not start the Daemon or mutate local state, and supports `--jsonl` for
Desktop callers. Desktop owns any last-successful-result cache.
The default terminal output is a compact compatibility table; add `--details`
to inspect the full manifest, immutable source URL, commit, and dependencies.
Use `--jsonl` only for Desktop or another machine caller.

`watcherobot app download --space-id ... --commit ... --target ...` publicly
downloads one immutable Space revision into a caller-created, existing empty
staging directory. It verifies the resolved commit, source limits, canonical
Manifest, fixed `app.py`, SDK compatibility, and the Space/App identity before
delivery. It needs no Hugging Face login, does not start the Daemon, does not
choose the final App Store directory or write `install.json`; use `app install`
for that atomic local-store operation. Both commands support `--jsonl` for
Desktop callers.

`watcherobot app install --space-id ... --commit ... --store-root ...
--runtime-root ...` downloads the reviewed immutable snapshot, verifies and
copies the locked Runtime when needed, creates that App's isolated Python
environment, validates dependencies and the entry point, then atomically writes
the installation record. `app list --store-root ...` reads installed records and
`app uninstall --store-root ... --app-id ...` moves one App to recoverable local
trash. These commands never start or contact the Daemon.

For larger Applications, add a `.watcherignore` file using glob patterns such as
`tests/`, `.venv*/`, and `*.tmp`; the ignore file itself is not included in the
published snapshot.

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
