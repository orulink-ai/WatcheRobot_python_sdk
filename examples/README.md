# Managed Application examples

Every example is a complete WatcheRobot Application with `app.json` and the
fixed `app.py` entrypoint. The program never opens device Discovery or a device
WebSocket; `ApplicationContext` receives its authorized desktop and device
channels from the SDK Runtime.

After installing the SDK with pip, switch between the bundled browser demos
without cloning this repository (these commands are included starting with the
next SDK release containing this change):

```text
watcherobot demo
```

Choose `1` for SDK Test Bench or `2` for Expression Lab. The interactive menu
stays open, so choosing another number switches directly. `0` stops the current
Application; `q` leaves the menu with the Application still running. Explicit
names (`watcherobot demo sdk-test-bench` / `watcherobot demo expression-lab`)
remain available for scripts and return after one launch.

Each selection stops the current Application through the existing Daemon, starts
the selected demo, and opens its browser UI. A compatible Daemon and its existing
device connection remain available. Use `watcherobot app stop` to
stop the active demo. Pair through the demo UI when no robot is connected.
Concurrent changes to the selected app are rejected rather than starting a
different app: retry the desired command. The demo CLI requires a Daemon that
supports selection-bound starts. If an older Daemon with the same SDK version
is already running (for example during source development), it fails before
stopping the current app; explicitly reload with `watcherobot daemon activate`
before retrying. Reloading stops the current Application and restarts Daemon.
Both examples currently declare Windows support; macOS/Linux hardware operation
has not been accepted. The CLI entrypoint itself is platform-independent.

The wheel contains the apps, web assets, sample audio, licenses, and Expression
Lab's firmware download resource. It excludes tests, virtual environments,
credentials, and captured artifacts. Runtime copies live below the SDK's user
state directory in `bundled-demos/<name>/<content-hash>/`; repeated launches reuse
the same copy and preserve its artifacts. An SDK update with changed resources
creates a new copy without deleting previous captures. No SDK installation files
are modified. Maintainers must add new demo resources to
`src/watcherobot/bundled-apps.json` before building a release.

For source development, run an example without the desktop:

```powershell
watcherobot app run ./examples/sdk_media_lab
```

`app run` accepts a source directory. Watcher Desktop installs reviewed fixed
commits from the selected Hugging Face or Gitee Application Store.

Pairing and device ownership remain in the long-lived Runtime. Stopping an
Application does not stop the Runtime or rebuild the device connection.

The repository contains two examples with browser interfaces. Headless examples
have been removed; SDK device APIs and Application templates remain available.

- `expression_lab`: launch a loopback-only animation workbench that previews
  and tunes the device-side procedural Watcher expression runtime.
- `sdk_media_lab`: launch a standalone loopback browser dashboard for speaker
  streaming, JPEG capture, microphone recording, artifacts, and diagnostics.

`vision_debug_lab` has been retired. The examples above do not replace all of its
dataset-recording and diagnostic-export workflows; see the
[retirement and migration notes](../docs/vision-diagnostics.md#vision-debug-lab-retirement-and-migration).
