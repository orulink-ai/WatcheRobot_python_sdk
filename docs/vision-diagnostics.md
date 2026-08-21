# Device vision diagnostics

`robot.vision` provides a model-independent health and capability view of the
vision backend currently active on the robot. Applications can use it as a
preflight check before starting a camera or inference workflow without knowing
whether the firmware is using the Himax PTL bridge or SSCMA inference runtime.

## Inspect the active backend

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        status = await asyncio.to_thread(app.robot.vision.status, timeout=5.0)
        app.logger.info(
            "backend=%s health=%s model=%s inference=%s preview=%s",
            status.backend,
            status.health,
            status.model.name if status.model else "unavailable",
            status.capabilities.inference,
            status.capabilities.preview,
        )


asyncio.run(main())
```

The same snapshot is available through these convenience methods:

- `robot.vision.health()` returns the complete `VisionStatus` snapshot.
- `robot.vision.active_model()` returns `VisionModel | None`.
- `robot.vision.capabilities()` returns `VisionCapabilities`.

The device must advertise `vision.status.v1`. Older firmware fails closed with
a capability error instead of guessing the backend state.

## Status contract

`VisionStatus` reports:

- backend name, health state, native status code, initialization state, and
  Himax connection state;
- whether the backend is currently streaming or inferencing;
- capture, preview, inference, model-information, and model-management support;
- the active model ID, name, task, and whether it contains a face class when
  the backend can expose that metadata.

The API is intentionally model-independent. An object detector, pose model,
gesture model, or face model uses the same status contract. Model-specific
output contracts remain separate capabilities; for example,
`robot.face_tracking.open_preview()` still requires
`face_tracking.preview.v1` because it returns face boxes and tracking telemetry.

## Backend expectations

| Backend | Capture / preview | Inference / model info | Notes |
| --- | --- | --- | --- |
| `ptl` | Available | Not available | JPEG transport firmware; use it for camera-path diagnostics. |
| `sscma` | Firmware-dependent | Available after initialization | Reports the active SSCMA model and inference health. |

`model_management` is currently `False` for both backends. Querying model
metadata is read-only. Model upload, replacement, and parameter mutation are
deliberately deferred until authorization, rollback, compatibility, and
firmware-recovery contracts are defined.

When SSCMA is already using the camera, the status call returns a busy snapshot
instead of competing for the Himax transport. Applications should retry later
with bounded backoff and should not bypass the Runtime by opening a second
device connection.

## Face-tracking preflight

Before starting face tracking, check both the generic backend state and the
model-specific preview capability:

```python
status = app.robot.vision.status(timeout=5.0)
if not status.capabilities.inference:
    raise RuntimeError(f"{status.backend} does not expose device inference")
if status.model is not None and not status.model.contains_face_class:
    raise RuntimeError(f"active model {status.model.name!r} has no face class")

with app.robot.face_tracking.open_preview() as preview:
    frame = preview.read(timeout=5.0)
```

See [Face-tracking preview API](face-tracking-preview.md) for the synchronized
JPEG and telemetry contract.

## Vision Debug Lab

`examples/vision_debug_lab` combines these preflight checks with a local
same-sequence face preview, latency/drop metrics, JPEG + JSONL recording,
HOLD/RECENTER controls, and diagnostic report export:

```shell
watcherobot app run ./examples/vision_debug_lab
```

It listens only on `127.0.0.1` and uses the Daemon-injected Application Device
channel rather than a robot LAN port. When the last browser viewer disconnects,
the service automatically applies HOLD. See the
[`Vision Debug Lab README`](../examples/vision_debug_lab/README.md) for the
complete operator workflow and backend limitations.
