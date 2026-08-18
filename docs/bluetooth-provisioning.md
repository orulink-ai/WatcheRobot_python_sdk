# Bluetooth Wi-Fi provisioning

`watcherobot.provisioning` configures the existing WatcheRobot ESP32 GATT
service from Python on Windows and macOS. It does not require the Runtime or
Daemon.

For normal first-time use, prefer the guided command that combines Bluetooth
Wi-Fi provisioning with Runtime pairing:

```powershell
watcherobot robot setup
```

The guided command asks the user to keep **Settings > Wi-Fi** open before it
scans. It identifies results by `BluetoothDevice.id`, offers an **Up/Down**
selector when several robots are nearby, and never treats the advertised name
as the pairing identity. After Wi-Fi provisioning, it guides the user to the
robot's **"Python SDK"** app and completes six-digit Runtime pairing in the same
setup flow.

The lower-level API and `watcherobot bluetooth ...` commands below are intended
for diagnostics and custom automation.

## Asynchronous API

```python
import asyncio

from watcherobot.provisioning import BluetoothProvisioner


async def main() -> None:
    provisioner = BluetoothProvisioner()
    devices = [
        device
        for device in await provisioner.scan_devices()
        if device.is_watcher
    ]
    if len(devices) != 1:
        raise RuntimeError("Select exactly one ESP_ROBOT device")

    result = await provisioner.provision_wifi(
        device=devices[0],
        ssid="MyWiFi",
        password="secret",
    )
    print(result.state)  # credentials_saved


asyncio.run(main())
```

`scan_devices()` keeps the operating system's native device object internally
so it can be passed directly to Bleak when connecting. A `BluetoothDevice.id`
is a Bluetooth address on Windows and a CoreBluetooth UUID on macOS; callers
must not assume that it is always a MAC address.

The other operations are:

```python
status = await provisioner.get_wifi_status(devices[0])
status = await provisioner.clear_wifi(devices[0])

result = await provisioner.provision_wifi(
    devices[0],
    ssid="Replacement WiFi",
    password="",
    clear_existing=True,
)
```

`clear_existing` defaults to `False`. An empty password is valid for an open
network. To match the currently shipped firmware's C-string storage boundary,
SSIDs must contain 1–31 UTF-8 bytes and passwords at most 63 UTF-8 bytes.
Neither value may contain an embedded NUL character.

## Result semantics

`credentials_saved` means that a `sys.ack` matching both
`cfg.wifi.set` and its `command_id` was received. It does not mean that the
SSID exists, the password is correct, DHCP succeeded, or the robot is online.
Use the later Wi-Fi status separately when needed.

The SDK subscribes to notifications before writing, writes with an ATT
response, and also reads the characteristic's cached response. After success,
timeout, cancellation, or another error, it makes bounded, independent
attempts to stop notifications and disconnect. Cleanup timeout or failure is
not allowed to replace an already acknowledged `credentials_saved` result.
Consequently, that result does not guarantee BLE disconnected or that the
firmware resumed its Wi-Fi connection attempt.

For `cfg.wifi.get` and `cfg.wifi.clear`, the shipped firmware places the
command ACK in the ATT write response, whose payload Bleak does not expose,
then publishes and caches `evt.wifi.status`. The SDK therefore accepts a valid
status obtained by the explicit post-write read as the observable success
signal. A status seen only through Notify remains a candidate until a matching
ACK/NACK arrives, so an unrelated early notification cannot hide rejection.

Default scan, connect, protocol-response, and per-cleanup-step timeouts are
10, 12, 3, and 2 seconds. They can be overridden when constructing
`BluetoothProvisioner`. Notification shutdown and disconnect are bounded
independently, so a stalled notification shutdown does not prevent the SDK
from attempting to disconnect. Cleanup is best-effort and its failure is not
reported separately by the current public result models.

## CLI

```text
watcherobot bluetooth scan
watcherobot bluetooth provision --device <id> --ssid <ssid> [--clear-existing]
watcherobot bluetooth status --device <id>
watcherobot bluetooth clear --device <id>
```

Every non-scan command scans again and resolves the exact platform device ID.
No device is selected automatically. The provisioning password is read with
an interactive, non-echoing prompt; there is deliberately no `--password`
option. Output uses compact JSON. `Ctrl+C` cancels the operation, performs BLE
cleanup, and exits with status 130.

## Physical-device smoke test

The repository includes an interactive test tool for exercising a real device
without putting the Wi-Fi password in shell history:

```text
python tools/ble_provisioning_hardware_test.py scan
python tools/ble_provisioning_hardware_test.py status --id-prefix 80:B5
python tools/ble_provisioning_hardware_test.py provision --id-prefix 80:B5 --ssid orulink
python tools/ble_provisioning_hardware_test.py clear --id-prefix 80:B5
```

Run these commands from an editable SDK checkout or an environment where the
SDK is installed. `provision` prompts for the password without echoing it.
`--id-prefix` is intended for Windows MAC-address prefixes; on macOS, copy the
CoreBluetooth UUID shown by `scan` and pass it through `--device`. A selector
must resolve to exactly one recognized `ESP_ROBOT`, otherwise the tool stops
without connecting.

## Protocol and security boundary

This release intentionally uses the firmware protocol without modifying it:

- device name: `ESP_ROBOT`
- service: `000000ff-0000-1000-8000-00805f9b34fb`
- characteristic: `0000ff01-0000-1000-8000-00805f9b34fb`
- compact UTF-8 JSON, at most 180 bytes per request

The protocol sends the SSID and password in JSON and adds no application-layer
authentication or encryption. This SDK release does not strengthen firmware
pairing, GATT permissions, transport encryption, MTU handling, or protocol
version negotiation. Provision only in a physically trusted environment and
treat access to the current characteristic as access to Wi-Fi credentials.

The SDK does not put the password in result reprs, logs, exceptions, CLI
arguments, parsed message models, or test snapshots. This protects ordinary
host-side diagnostics but does not change the on-air security of the existing
firmware protocol.
