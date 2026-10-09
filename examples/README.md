# Managed Application examples

Every example is a complete WatcheRobot Application with `app.json` and the
fixed `app.py` entrypoint. The program never opens device Discovery or a device
WebSocket; `ApplicationContext` receives its authorized desktop and device
channels from the SDK Runtime.

Run an example without the desktop:

```powershell
watcherobot app run ./examples/sdk_media_lab
```

`app run` accepts a source directory. Watcher Desktop installs reviewed fixed
commits from the selected Hugging Face or Gitee Application Store.

Pairing and device ownership remain in the long-lived Runtime. Stopping an
Application does not stop the Runtime or rebuild the device connection.

The repository contains three examples with browser interfaces. Headless examples
have been removed; SDK device APIs and Application templates remain available.

- `codex_voice_app`: bridge the robot's native full-duplex RTC audio to the
  Codex voice frontend, with permission-gated robot tools, a local fixed-voice
  playback test, and audio diagnostics. See its [README](codex_voice_app/README.md)
  for setup and known limitations; intermittent playback gaps, adaptive device
  buffer feedback, and the web pairing-code entry remain unresolved.
- `expression_lab`: launch a loopback-only animation workbench that previews
  and tunes the device-side procedural Watcher expression runtime.
- `sdk_media_lab`: launch a standalone loopback browser dashboard for speaker
  streaming, JPEG capture, microphone recording, artifacts, and diagnostics.

`vision_debug_lab` has been retired. The examples above do not replace all of its
dataset-recording and diagnostic-export workflows; see the
[retirement and migration notes](../docs/vision-diagnostics.md#vision-debug-lab-retirement-and-migration).
