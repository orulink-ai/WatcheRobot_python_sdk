# Managed Application examples

Every example is a complete WatcheRobot Application with `app.json` and the
fixed `app.py` entrypoint. The program never opens device Discovery or a device
WebSocket; `ApplicationContext` receives its authorized desktop and device
channels from the SDK Runtime.

Run an example without the desktop:

```powershell
watcherobot app run .\examples\hello_robot
```

Package and run it through the Catalog:

```powershell
watcherobot app package .\examples\hello_robot .\dist\hello_robot.wapp
watcherobot app run .\dist\hello_robot.wapp
```

Pairing and device ownership remain in the long-lived Runtime. Stopping an
Application does not stop the Runtime or rebuild the device connection.
