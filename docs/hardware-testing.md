# Hardware testing

Hardware acceptance now uses a managed Application rather than a second SDK
gateway. Start a Runtime, pair the device through the Runtime control surface,
and run an Application that exercises the required capabilities:

```powershell
watcherobot daemon start
watcherobot app run .\examples\quickstart
```

Record the SDK version, Runtime log, firmware commit/version, pairing result,
Application result, and any camera or microphone artifacts. A protocol fake is
useful for automated regression but does not replace real-device acceptance.

Camera and microphone tests may capture people nearby. Obtain consent and
protect or remove generated artifacts after validation.
