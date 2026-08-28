# Managed Application examples

Every example is a complete WatcheRobot Application with `app.json` and the
fixed `app.py` entrypoint. The program never opens device Discovery or a device
WebSocket; `ApplicationContext` receives its authorized desktop and device
channels from the SDK Runtime.

Run an example without the desktop:

```powershell
watcherobot app run .\examples\hello_robot
```

`app run` accepts a source directory. Watcher Desktop installs reviewed Hugging
Face fixed commits through its Application Store.

Pairing and device ownership remain in the long-lived Runtime. Stopping an
Application does not stop the Runtime or rebuild the device connection.

Available examples include:

- `expression_lab`: launch a loopback-only animation workbench that previews
  and tunes the device-side procedural Watcher expression runtime.
- `dshtts_speaker`: expose a loopback TTS bridge that converts DSH assistant
  replies with edge-tts and plays them through the robot speaker; includes
  matching Windows PowerShell and macOS/Linux shell clients.
- `vision_debug_lab`: launch a loopback-only Himax vision workbench for
  backend/model health, same-sequence face overlays, metrics, dataset
  recording, HOLD/RECENTER safety, and diagnostic reports.
- `scheduled_reminder`: a robot alarm clock app — set alarms (time, repeat
  rules, text) on its local web page (http://127.0.0.1:8766), and the robot
  speaks them from its speaker at the scheduled times, playing a happy
  behavior to draw attention.
- `sdk_media_lab`: launch a standalone loopback browser dashboard for speaker
  streaming, JPEG capture, microphone recording, artifacts, and diagnostics.
- `capture_photo`: capture one JPEG from the camera.
- `record_microphone`: record a short WAV file.
- `face_tracking_preview`: consume typed, sequence-matched live preview frames
  without opening a device socket in Application code.
