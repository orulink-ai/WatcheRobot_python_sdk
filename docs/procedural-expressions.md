# Device-side procedural expressions

`app.robot.expression_runtime` controls the negotiated
`expression.runtime.v2` capability. The Application sends compact parameters;
the ESP32 generates RGB565 frames locally and keeps the Daemon content-agnostic.

```python
await asyncio.to_thread(
    app.robot.expression_runtime.start,
    "thinking",
    style="watcher_focus",
    gaze_x=0.25,
    gaze_y=-0.1,
    openness=0.72,
    spacing=0.85,
    scale=1.0,
    scale_x=2.0,
    scale_y=2.0,
    stroke=1.0,
    roundness=1.0,
    left_openness=0.85,
    right_openness=1.0,
    tilt_deg=-7,
    left_tilt_deg=-3,
    right_tilt_deg=3,
    tag="thinking",
    accessory="halo",
    accessory_scale=1.15,
    accessory_x=0.0,
    accessory_y=-0.1,
    accessory_rotation_deg=-8,
    auto_blink=True,
    blink_interval_ms=3600,
    blink_duration_ms=200,
    color="#A1F03C",
    transition_ms=180,
)

await asyncio.to_thread(
    app.robot.expression_runtime.update,
    gaze_x=-0.4,
    openness=0.85,
    transition_ms=120,
)

await asyncio.to_thread(app.robot.expression_runtime.stop)
```

The runtime exposes three presets (`standby`, `thinking`, and `speaking`), five
Watcher styles, four optional tags, six built-in accessories (`halo`,
`devil_horns`, `ninja_mask`, `hero_mask`, `eyepatch`, and `antenna`), uniform and independent width/height scale, stroke and
roundness, per-eye openness and tilt, blink timing, and RGB color. The default
Watcher geometry is 2× the original PoC eye size with tighter 0.85 spacing.
Calls fail before
transport when a value is outside its supported range, and fail with
`WatcheRobotError` when the connected firmware does not advertise
`expression.runtime.v2`.

While this runtime is active it does not read or decode AnimPack assets. The
SDK experiment deliberately leaves the existing AnimPack APIs intact as a
rollback path; removing those APIs and their assets belongs to the later
product migration, after device performance and visual parity are accepted.

For visual tuning, run the loopback-only workbench:

```powershell
watcherobot app run .\examples\expression_lab
```

If the workbench is waiting for a device, open **Desktop Link** on Watcher and
enter its six-digit pairing code in the workbench header. Pairing remains a
Runtime management operation; the browser never opens a serial or device
business connection.

The browser preview is a design aid. Device FPS, LCD transfer time, memory
headroom, and final pixels must still be verified on physical hardware.
