# WatcheRobot CLI reference

This is the complete reference for the installed `watcherobot` command. Use
`watcherobot <group> <command> --help` to inspect the options exposed by the
installed SDK version.

For an end-to-end Application publishing walkthrough, see the
[SDK Application guide](application-marketplace/sdk-application-usage.md).
For the JSONL event and error contract used by Desktop, see the
[Application distribution contract](application-marketplace/distribution-contract.md).

## Command map

```text
watcherobot
├─ daemon      start | status | stop
├─ robot       setup | pair | status
├─ app         init | check | run | run-installed | start | stop
│              login | logout | publish | submit | marketplace
│              download | install | list | uninstall
└─ bluetooth   scan | provision | status | clear
```

Use `watcherobot --version` to print the installed SDK version without starting
the Runtime or loading a project.

## Output and safety conventions

- Commands print human-readable output by default. Application distribution
  commands also accept `--jsonl` for Desktop and other automation; it emits
  only JSON Lines on stdout.
- `watcherobot app run`, `run-installed`, `start`, `stop`, and `robot pair`
  start or reuse a Runtime as needed. Distribution and Bluetooth commands do
  not.
- A Runtime owns pairing and the only device connection. Do not make an
  Application open a second discovery socket or device WebSocket.
- `app install`, `list`, and `uninstall` operate on an explicit SDK App Store.
  Desktop supplies its own store and locked Runtime path in production.

## Runtime commands

### `watcherobot daemon start`

Starts the current user's Runtime, or reuses it when it is already healthy.
It prints the process ID and local `control_url`; use that URL only for local
Runtime control APIs. Starting the Runtime does not pair a device or start an
Application.

```powershell
watcherobot daemon start
```

### `watcherobot daemon status`

Reports whether the current-user Runtime is alive. When it is running, the
output also includes the process ID, control URL, and Runtime status. It exits
with code `1` when no Runtime is running, so scripts can use it as a probe.

```powershell
watcherobot daemon status
```

### `watcherobot daemon stop`

Requests the current-user Runtime to stop. This also stops a running managed
Application. Do not use it to stop a Runtime owned by Watcher Desktop while
Desktop is still using it; stop the Application or exit Desktop instead.

```powershell
watcherobot daemon stop
```

## Robot onboarding commands

These are the normal user-facing commands for connecting hardware. Use the
lower-level `bluetooth` group only for provisioning diagnostics or custom
automation.

### `watcherobot robot setup [--device <id>] [--ssid <name>] [--pairing-code <code>] [--clear-existing]`

Guides first-time setup end to end. It first asks the user to turn on computer
Bluetooth and open **Settings > Wi-Fi** on the robot, then starts scanning only
after confirmation.
Results are shown by the stable **Device ID** advertised by the robot. With
multiple results, use **Up/Down** and Enter to choose the intended Device ID;
device names are not used as the selection identity. Older firmware without an
advertised Device ID is marked as unavailable and exposes the Bluetooth ID only
as a compatibility fallback. `--device` accepts the Device ID and continues to
accept that fallback Bluetooth ID for older firmware.

The guided flow separates recoverable states instead of returning raw scan
logs:

| State | What the command explains | Recovery |
|---|---|---|
| Computer Bluetooth is off or unavailable | Bluetooth cannot be used | Turn on Bluetooth or check the adapter, then rerun setup |
| Operating-system permission is denied | Bluetooth access was denied | Allow the terminal or Python to use Bluetooth, then rerun setup |
| No robot is found | No provisioning advertisement was discovered | Keep **Settings > Wi-Fi** open and the robot nearby; already-networked robots use `robot pair` |
| One or several current robots are found | Stable Device IDs are displayed | Select the Device ID shown on the robot with **Up/Down** |
| Older firmware does not advertise a Device ID | Device ID is unavailable and a firmware update may be required | Bluetooth ID remains only as a compatibility fallback |
| Connection or firmware response times out | Bluetooth communication did not complete | Keep the robot nearby, close competing Bluetooth apps, and retry |
| The operation is cancelled | Setup was cancelled | No credentials or pairing code are printed |

The command then reads the Wi-Fi password privately and provisions the
credentials. To finish, return to the robot launcher, open the **"Python SDK"**
app, read the six-digit code at the top of its screen, and enter it into the
same setup flow. Omitted values are prompted in an interactive terminal. The
password is never accepted as an argument or printed.

```powershell
watcherobot robot setup
```

For automation, non-secret values may be supplied explicitly while the
password remains interactive:

```powershell
watcherobot robot setup `
  --device <bluetooth-id> `
  --ssid MyWiFi `
  --pairing-code 123456
```

### `watcherobot robot pair <six-digit-code>`

Pairs a robot that is already on the same network. Open the robot's **"Python
SDK"** app first and use its current code. The command starts or reuses the
Runtime, initiates the `python_sdk` pairing mode, waits for the device
connection, and reports common discovery or connection failures in user terms.

```powershell
watcherobot robot pair 123456
```

### `watcherobot robot status`

Reports the actual Runtime-owned robot connection. It exits with code `0` when
online and `1` when no robot is connected. A stopped Runtime is reported as a
disconnected robot with the next setup command.

```powershell
watcherobot robot status
```

## Application commands

Every Application has a canonical `app.json` manifest and fixed `app.py`
entrypoint. `app run` accepts a source directory for development; it is not a
shortcut for directly executing `app.py`.

### Create, validate, and run

#### `watcherobot app init [directory]`

Creates a runnable Hello World Application without overwriting an existing
target. When the directory is omitted, an interactive terminal prompts for it.
That directory is the only interactive question: ID, display name, author, and
description are generated automatically. For example, `my_app` receives the
stable, readable ID `local.my_app`. Publishing metadata can be overridden later.

```powershell
watcherobot app init my_app

watcherobot app init published_app `
  --id com.example.my_app `
  --name "My App" `
  --author "Example Team" `
  --description "An example WatcheRobot Application"
```

If an explicit directory is followed by `Application ID:` or other metadata
prompts, the terminal is running an older CLI. Activate the intended virtual
environment and check the command source with `where.exe watcherobot`. An
Application ID is a stable upgrade identity, so the initializer does not append
a username, timestamp, or random value. Published apps should use a stable
team-owned namespace such as `com.example.my_app`.

It creates `app.json`, `app.py`, `README.md`, `icon.svg`, and `.gitignore`.
The generated `app.py` always logs a Hello World success. When a compatible
robot is connected, it also plays the `happy` behavior once.

#### `watcherobot app check <directory>`

Checks the manifest, fixed entrypoint, SDK compatibility, normal Python
requirements, icon path, and publishable source files. It does not start the
Runtime and changes no local or remote state.

```powershell
watcherobot app check .\my_app
watcherobot app check .\my_app --jsonl
```

#### `watcherobot app run [directory]`

Starts or reuses the current-user Runtime, selects the local source Application,
and launches it with Runtime-injected `WATCHER_APP_*` variables. The directory
defaults to the current working directory. The Runtime remains available after
the Application exits. When no robot is connected, the CLI prints the exact
first-time `watcherobot robot setup` command and the already-networked
`watcherobot robot pair <code>` shortcut, then continues so offline
Applications still work.

```powershell
cd my_app
watcherobot app run
```

#### `watcherobot app run-installed --store-root <path> --app-id <id>`

Runs an Application already installed into a custom SDK App Store. This is the
SDK developer and acceptance-test path: it starts or reuses an isolated
Runtime rooted at that store and uses ephemeral ports. It never reuses or
modifies the Desktop Runtime.

```powershell
watcherobot app run-installed `
  --store-root .\staging\app-store `
  --app-id com.example.my_app
```

#### `watcherobot app start` and `watcherobot app stop`

`start` launches the Application currently selected by the Runtime; `stop`
stops that running Application while leaving the Runtime alive. Neither command
selects a different Application. They start or reuse the current-user Runtime.

```powershell
watcherobot app start
watcherobot app stop
```

### Authenticate, publish, and submit

#### `watcherobot app login [--status | --force]`

Uses the Watcher Desktop public OAuth Device Flow to authorize publishing to
Hugging Face. The default flow prints a URL and user code; `--status` checks
the saved identity without opening a flow; `--force` replaces a valid saved
login. The token is stored only in Watcher's operating-system credential entry.

```powershell
watcherobot app login
watcherobot app login --status
watcherobot app login --force
```

#### `watcherobot app logout`

Removes only Watcher's saved Hugging Face credential. It does not sign out the
Hugging Face CLI or another program.

```powershell
watcherobot app logout
```

#### `watcherobot app publish <directory>`

Validates the local project and uploads its exact source snapshot to the public
`<username>/WatcherRobot-<app_id>` Hugging Face Space. It returns the immutable
source commit, but does not create a catalog entry or start the Runtime.

```powershell
watcherobot app publish .\my_app
```

#### `watcherobot app submit <directory> [--commit <sha>]`

Verifies a published immutable snapshot and opens or reuses the official
Marketplace pull request. `author` and `description` must be present. Omit
`--commit` to submit the current Space HEAD; provide a 40-character commit to
review one exact revision. This command never uploads source.

```powershell
watcherobot app submit .\my_app
watcherobot app submit .\my_app --commit <40-character-commit>
```

#### `watcherobot app marketplace [--details | --jsonl]`

Reads and validates the reviewed public Marketplace. Default output is a
compact compatibility table; `--details` adds the full manifest, source URL,
commit, and dependencies. `--jsonl` is the machine form. It needs no login,
does not start the Runtime, and does not write a cache.

```powershell
watcherobot app marketplace
watcherobot app marketplace --details
```

### Download and manage reviewed Applications

#### `watcherobot app download --space-id <id> --commit <sha> --target <empty-directory>`

Downloads one reviewed immutable Space revision into an existing, empty staging
directory. Before delivery, it verifies the commit, source limits, manifest,
fixed entrypoint, SDK compatibility, and Space/Application identity. It does
not create the target, install the Application, or write `install.json`.

```powershell
watcherobot app download `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --target .\staging\app
```

#### `watcherobot app install --space-id <id> --commit <sha> --store-root <path> --runtime-root <path>`

Downloads a reviewed immutable revision, verifies it, copies the supplied
locked Runtime when needed, creates the Application's isolated Python
environment, and atomically writes the installation record. It never starts
or contacts the Runtime.

```powershell
watcherobot app install `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --store-root <app-store-directory> `
  --runtime-root <locked-app-runtime-directory>
```

#### `watcherobot app list --store-root <path>`

Lists the records installed in one SDK App Store. It reads only local records;
it does not query the Marketplace or start the Runtime.

```powershell
watcherobot app list --store-root <app-store-directory>
```

#### `watcherobot app uninstall --store-root <path> --app-id <id>`

Moves one installed Application to recoverable local trash. It does not delete
the Marketplace source and does not start the Runtime. Stop a running App
before uninstalling it.

```powershell
watcherobot app uninstall `
  --store-root <app-store-directory> `
  --app-id <application-id>
```

## Advanced Bluetooth Wi-Fi provisioning commands

Most developers should use `watcherobot robot setup`. These lower-level
commands use the device's existing `ESP_ROBOT` BLE GATT service. They
support Python 3.10–3.12 on Windows and macOS. The provisioning command reads
the password interactively and never accepts it as a command-line argument.

### `watcherobot bluetooth scan`

Scans for compatible Bluetooth devices and prints their identifiers. Pass the
reported ID to the other Bluetooth commands.

```powershell
watcherobot bluetooth scan
```

### `watcherobot bluetooth provision --device <id> --ssid <name> [--clear-existing]`

Prompts for the Wi-Fi password and sends credentials to the selected device.
`--clear-existing` asks the device to clear saved credentials first. A
`credentials_saved` result confirms only that firmware stored the credentials;
it does not prove that the device joined the network.

```powershell
watcherobot bluetooth provision --device <id> --ssid MyWiFi
```

### `watcherobot bluetooth status --device <id>`

Reads the Wi-Fi provisioning status currently reported by the device.

```powershell
watcherobot bluetooth status --device <id>
```

### `watcherobot bluetooth clear --device <id>`

Requests removal of the Wi-Fi credentials stored on the selected device and
prints its resulting status.

```powershell
watcherobot bluetooth clear --device <id>
```

See [Bluetooth provisioning](bluetooth-provisioning.md) for the BLE protocol,
timeouts, cleanup behavior, and security boundaries.

## `watcher-distribution` sidecar

Desktop packages may invoke `watcher-distribution app` instead of
`watcherobot app` for short-lived distribution work. It supports exactly these
Application commands: `check`, `login`, `logout`, `publish`, `submit`,
`marketplace`, `download`, `install`, `list`, and `uninstall`.

Its syntax, options, behavior, and JSONL output are identical to the matching
`watcherobot app` commands above. It cannot run `init`, `run`, `run-installed`,
`start`, or `stop`, and never imports or starts the Runtime.

```powershell
watcher-distribution app check .\my_app --jsonl
watcher-distribution app marketplace --jsonl
```
