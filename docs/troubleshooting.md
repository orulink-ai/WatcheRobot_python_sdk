# Runtime and Application troubleshooting

Start with the Runtime and managed Application state:

```powershell
watcherobot daemon status
watcherobot app list
```

| Symptom | Likely cause | Action |
|---|---|---|
| Runtime does not start | Ports are occupied or another user-session Runtime owns the lock | Read `%LOCALAPPDATA%\WatcheRobot\runtime\runtime.log`; stop the existing Runtime explicitly |
| Desktop Daemon log is empty | The desktop adopted a Runtime that it did not spawn, so its stdout pipe is unavailable | Read `GET /daemon/logs` or `<Runtime state root>\logs\daemon.jsonl`; standalone CLI defaults to `%LOCALAPPDATA%\WatcheRobot\runtime`, while Desktop supplies its own state root and polls the Runtime endpoint automatically |
| `Application environment is incomplete` | `app.py` was launched directly | Use `watcherobot app run <directory-or-wapp>` or start it from the desktop |
| `invalid_application` | `app.json`, fixed `app.py`, id, or version requirement is invalid | Validate the manifest and `requires_watcherobot` |
| `application_occupied` | Another Application process is starting or running | Stop it before selecting, installing, or starting another one |
| `Application startup timed out` during a cold default-Application launch | Older Runtime builds allowed only 30 seconds, while a first Windows launch can need more time to initialize | Update the packaged SDK Runtime. Current builds allow 90 seconds in the Daemon and 120 seconds in Desktop before declaring failure |
| Command timeout | The device is offline or did not acknowledge the frame | Check `/daemon/devices`, pairing state, firmware logs, and the Runtime log |
| `CommandError: ... not_found` | The requested resource is not installed in firmware | Use a resource ID supported by the current firmware |
| Firmware flashing disconnects after switching to 921600 baud | The USB serial path is unstable at the fast baud rate | Current maintenance builds close the failed esptool process, re-enter the ROM downloader, and retry the complete flash once at 460800 baud; keep the USB cable connected until the task reaches 100% |

The Application process never owns device Discovery or pairing. Device
connectivity problems should be diagnosed at the Runtime boundary, while
business errors and stdout/stderr are diagnosed in the Application log.
