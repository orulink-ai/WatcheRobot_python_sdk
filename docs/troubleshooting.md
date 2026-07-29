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
| Command timeout | The device is offline or did not acknowledge the frame | Check `/daemon/devices`, pairing state, firmware logs, and the Runtime log |
| `CommandError: ... not_found` | The requested resource is not installed in firmware | Use a resource ID supported by the current firmware |

The Application process never owns device Discovery or pairing. Device
connectivity problems should be diagnosed at the Runtime boundary, while
business errors and stdout/stderr are diagnosed in the Application log.
