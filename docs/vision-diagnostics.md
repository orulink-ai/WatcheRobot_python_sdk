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

## Managed preview example

`examples/face_tracking_preview` consumes sequence-matched preview frames
through the managed Application Device channel:

```shell
watcherobot app run ./examples/face_tracking_preview
```

It logs face telemetry and saves the latest JPEG after collecting 150 frames.
It does not provide a browser dashboard. Use the SDK APIs above for backend
health and capability checks in your own Application.

## Vision Debug Lab retirement and migration

`examples/vision_debug_lab` was explicitly retired at the maintainer's request
because that application was outdated. Its removal is an intentional breaking
change to the example launch command, not a removal of SDK camera or vision
APIs. `face_tracking_preview` is a smaller example, **not an equivalent replacement**.

| Previous workflow | Available path and limitation |
| --- | --- |
| Browser face preview with matching boxes | `sdk_media_lab` supports face preview; this is not the old diagnostic dashboard. |
| Sequence-matched JPEG and telemetry consumption | `face_tracking_preview` demonstrates the SDK API and saves only the last JPEG after 150 frames. |
| JPEG + JSONL dataset recording | No equivalent recorder is supplied; an Application must implement recording through the preview API. |
| Vision-specific latency/drop reports and export | No equivalent report export is supplied; retain the old tool if this is required. |
| Last-browser disconnect automatically applies HOLD | Not guaranteed by the CLI preview example; Applications must manage tracking shutdown explicitly. |

For workflows that still require the complete old tool, retain its source from
the parent of removal commit `2e60466` in an isolated checkout. This is a legacy
source reference, not a claim that the old tool has been validated with current
firmware. Existing datasets should be kept separately before changing versions.
