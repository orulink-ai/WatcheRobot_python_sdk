# Hardware testing

Hardware acceptance now uses a managed Application rather than a second SDK
gateway. Start the Runtime, replace `123456` with the six-digit code shown by
the device, and pair it through the Runtime control API:

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
```

After the device state reaches `connected`, run managed Applications that
exercise the required capabilities:

```powershell
watcherobot app run .\examples\quickstart
watcherobot app run .\examples\capture_photo
watcherobot app run .\examples\record_microphone
```

For an operator-facing media bench, run the standalone SDK Media Lab:

```powershell
watcherobot app run .\examples\sdk_media_lab
```

It binds a temporary dashboard to `127.0.0.1`, opens the default browser, and
keeps every hardware operation inside the managed Application Device channel.
The dashboard tests the bundled speaker stream, one-shot JPEG capture, decoded
microphone recording, capability discovery, artifacts, and diagnostic events.
It is independent of Watcher Desktop and stops with the Application process.

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
