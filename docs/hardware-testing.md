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

Record the SDK version, Runtime log, firmware commit/version, pairing result,
Application result, and any camera or microphone artifacts. Runtime and
Application automated tests are useful for regression, but they do not replace
real-device acceptance.

Camera and microphone tests may capture people nearby. Obtain consent and
protect or remove generated artifacts after validation.
