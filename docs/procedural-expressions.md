# Device-side procedural expressions

`app.robot.expression_runtime` controls the negotiated
`expression.runtime.v3` capability. The Application sends compact parameters;
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
    left_upper_lid_y=-30,
    left_upper_lid_rotation_deg=-14,
    right_upper_lid_y=-30,
    right_upper_lid_rotation_deg=14,
    left_lower_lid_y=65,
    left_lower_lid_rotation_deg=0,
    right_lower_lid_y=65,
    right_lower_lid_rotation_deg=0,
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
    sphere_strength=0.68,
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
roundness, per-eye openness and tilt, four independent eyelid masks, blink timing, RGB color, and an optional
precomputed sphere projection (`sphere_strength` from `0.0` to `1.0`). A value
of `0.0` keeps the exact flat renderer. The default
Watcher geometry is 2× the original PoC eye size with tighter 0.85 spacing.
Calls fail before
transport when a value is outside its supported range, and fail with
`WatcheRobotError` when the connected firmware does not advertise
`expression.runtime.v3`.

Each eye has an upper and lower mask. The four `*_lid_y` values use logical
coordinates from `-80` to `80`; each matching `*_lid_rotation_deg` accepts
`-45` to `45`. Neutral values place upper masks at `-80` and lower masks at
`80`. The masks follow their eye's gaze center, interpolate with
`transition_ms`, affect only eye-colored pixels, and are included before the
sphere projection. This keeps tags and accessories on independent layers.

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

The Expression Lab enables pointer gaze by default. Pointer coordinates inside
the 412 × 412 preview are normalized to `gaze_x` and `gaze_y`, multiplied by a
configurable `0.60`–`2.00` response gain (`1.45` by default), and damped locally.
The current Expression Lab fixes `sphere_strength` to `0` and coalesces pointer
updates for the flat renderer; leaving the preview returns the target to the neutral gaze. This
reuses the normal Application → Daemon → Device channel and never streams
browser pixels to Watcher.

At the normalized `gaze_x` / `gaze_y` limits, both renderers move the eye group
by 32 physical pixels (16 logical pixels at the 2× native render scale).

The current workbench exposes only the bounded vector accessory editor. A
custom vector uses physical 412 × 412 coordinates and supports at most 12
strokes, 48 points per stroke, and 192 points in total. The browser simplifies
the path on pointer-up and sends it once; the device validates and renders the
stored path locally. Arbitrary SVG, Canvas commands, JavaScript, filled paths,
gradients, and textures are not executed on the device. The retired pixel
editor is no longer shown by Expression Lab, although the firmware temporarily
keeps its old protocol capability for backward compatibility.
