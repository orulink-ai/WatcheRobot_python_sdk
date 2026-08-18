# Runtime and Application troubleshooting

Start with the Runtime and managed Application state:

```powershell
watcherobot daemon status
watcherobot robot status
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
| `No robot is connected` | The robot has not completed Wi-Fi setup and Runtime pairing, or it is offline | Run `watcherobot robot setup`; if Wi-Fi is already configured, use `watcherobot robot pair <six-digit-code>` |
| `Bluetooth is unavailable on this computer` | Bluetooth is off, no adapter is available, or the operating system reports it unavailable | Turn on Bluetooth, confirm the adapter is available, then rerun `watcherobot robot setup` |
| `Bluetooth access was denied` | The terminal or Python lacks operating-system Bluetooth permission | Allow Bluetooth access in system privacy settings, then rerun setup |
| `Device ID unavailable` | The robot advertises for provisioning but its firmware does not include the stable Device ID Service Data | Update robot firmware when available; Bluetooth ID is retained only for compatibility |
| `Robot rejected the Wi-Fi settings` | The firmware rejected the supplied network configuration | Check the Wi-Fi name and password, then rerun setup |
| `Robot did not respond in time` | The BLE connection was interrupted or the firmware did not answer | Keep the robot nearby on **Settings > Wi-Fi**, close competing Bluetooth apps, and retry |
| `incompatible Bluetooth response` | The SDK and robot firmware provisioning protocols do not match | Update both the SDK and firmware; report both versions if the error persists |
| `Robot pairing could not be completed` | Runtime pairing did not finish after Wi-Fi setup | Keep the robot's **"Python SDK"** app open, confirm both devices use the same network, and enter the latest code |
| `pairing_not_found` | The computer and robot are not on the same network, or the displayed code expired | Confirm both devices use the same network and retry `watcherobot robot pair` with the current code |
| Command timeout | The device is offline or did not acknowledge the frame | Check `/daemon/devices`, pairing state, firmware logs, and the Runtime log |
| `CommandError: ... not_found` | The requested resource is not installed in firmware | Use a resource ID supported by the current firmware |
| Firmware flashing disconnects after switching to 921600 baud | The USB serial path is unstable at the fast baud rate | Current maintenance builds close the failed esptool process, re-enter the ROM downloader, and retry the complete flash once at 460800 baud; keep the USB cable connected until the task reaches 100% |

The Application process never owns device Discovery or pairing. Device
connectivity problems should be diagnosed at the Runtime boundary, while
business errors and stdout/stderr are diagnosed in the Application log.
