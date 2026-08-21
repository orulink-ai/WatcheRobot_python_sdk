# Hardware testing

Hardware acceptance uses a managed Application rather than a second SDK
gateway. For a new or reset robot, complete Wi-Fi provisioning and pairing in
one guided command:

```powershell
watcherobot robot setup
```

If Wi-Fi is already configured, replace `123456` with the current six-digit
code shown by the device and pair directly:

```powershell
watcherobot robot pair 123456
watcherobot robot status
```

The CLI selects the `python_sdk` target mode for SDK testing and waits until
the Runtime reports a connected device. The Daemon validates connection modes
but does not route business frames by message content.

After the device state reaches `connected`, run managed Applications that
exercise the required capabilities:

```powershell
watcherobot app run .\examples\quickstart
watcherobot app run .\examples\capture_photo
watcherobot app run .\examples\record_microphone
```

For Himax and on-device face-tracking acceptance, run Vision Debug Lab:

```powershell
watcherobot app run .\examples\vision_debug_lab
```

It binds only to `127.0.0.1` and uses the Runtime-injected Application Device
channel. It does not connect to a robot LAN port. The dashboard checks the
vision backend, Himax connection, current model and capabilities before it
opens preview. It then displays sequence-matched JPEG and face telemetry,
collects latency/drop metrics, records JPEG + JSONL datasets, and exports a
diagnostic report. Closing the last dashboard viewer automatically applies
HOLD.

PTL firmware can validate the JPEG path but cannot provide face inference.
SSCMA firmware must expose both `vision.status.v1` and
`face_tracking.preview.v1`, and the active model must contain a face class.
Model metadata is read-only in this release; model upload, switching and
parameter tuning remain outside the Application contract.

On the dual CH342 Type-C bridge, CH342-B is the ESP32 log/flash channel and
CH342-A can connect to the Himax maintenance UART. Both ports can be opened at
the same time, and passive Himax UART reading does not block ESP32 SPI
inference. Keep high-bandwidth preview on the managed Application path and use
the UART for low-level maintenance or low-volume logs so display animation,
preview transport, and verbose serial output do not compete unnecessarily.

For an operator-facing media bench, run the standalone SDK Media Lab:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

It binds a temporary dashboard to `127.0.0.1`, opens the default browser, and
keeps every hardware operation inside the managed Application Device channel.
The dashboard tests the bundled speaker stream, one-shot JPEG capture, decoded
microphone recording, capability discovery, artifacts, diagnostic events,
device resource recovery, and full-duplex RTC audio. It is independent of
Watcher Desktop and stops with the Application process.

For RTC audio acceptance, use headphones and verify both directions separately:

- speak to the robot and confirm its physical microphone is audible in the
  headphones without enabling local browser microphone monitoring;
- speak to the computer and confirm the robot speaker is audible while delayed
  self-voice is suppressed by the device-side AEC;
- confirm `audio_aec_reference_drops`, `audio_render_errors`, and
  `audio_queue_dropped` remain zero;
- repeat start/stop at least ten times and confirm resource snapshots return to
  a stable idle range rather than declining monotonically.

Record the SDK version, Runtime log, firmware commit/version, pairing result,
Application result, and any camera or microphone artifacts. Runtime and
Application automated tests are useful for regression, but they do not replace
real-device acceptance.

For a microphone acceptance, use `robot.microphone.record_pcm()`, save the
result as WAV, and verify all of the following:

- the WAV header declares 16 kHz, mono, signed 16-bit PCM;
- its duration is derived from PCM bytes and agrees with the recorded interval;
- playback is intelligible rather than compressed-packet noise; and
- the returned `recording.decode_failures` stays at zero for the tested stream.

The device transport remains Opus. The Runtime/Daemon forwards those WSPK
payloads without decoding them; the managed Application's SDK decodes them.

Camera and microphone tests may capture people nearby. Obtain consent and
protect or remove generated artifacts after validation.
