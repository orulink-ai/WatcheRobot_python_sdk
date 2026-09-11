# Face-tracking preview API

`robot.face_tracking` exposes the Watcher Himax face-following preview to a
managed Python Application. The Runtime remains the only component that owns
pairing, the device connection, UDP reassembly, and Application routing.

For a model-independent preflight check, query `robot.vision.status()` first.
It reports the active PTL or SSCMA backend, health, generic inference support,
and active-model metadata. See [Device vision diagnostics](vision-diagnostics.md).

## Start and consume a preview

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        preview = await asyncio.to_thread(
            app.robot.face_tracking.open_preview,
            width=640,
            height=480,
            frame_stride=1,
            stop_policy="hold",
        )
        async with preview:
            async for frame in preview:
                # JPEG bytes can be forwarded to a WebSocket, decoder, or file.
                jpeg = frame.jpeg
                faces = frame.faces
                tracking = frame.telemetry
                app.logger.info(
                    "seq=%d jpeg=%d faces=%d age=%dms inference=%s",
                    frame.sequence,
                    len(jpeg),
                    len(faces),
                    tracking.age_ms,
                    tracking.inference_ms,
                )


asyncio.run(main())
```

Synchronous code can use `with` and `preview.read(timeout=...)`. Asynchronous
code can use `async with`, `async for`, or
`await preview.read_async(timeout=...)`.

## Frame contract

Each `FaceTrackingFrame` contains a JPEG and telemetry with the same device
sequence number. It is never a JPEG paired with an older or newer face box.

- `jpeg`: encoded JPEG bytes.
- `sequence`: device frame sequence.
- `width`, `height`: actual JPEG sensor dimensions.
- `faces`: immutable `FaceBox` values in sensor pixel coordinates.
- `telemetry`: target visibility, tracking error, commanded axis velocities,
  algorithm state, and Himax stage timings.
- `received_at`: host wall-clock time when the complete pair was assembled.

The default queue size is one. If Application processing is slower than the
camera, the SDK replaces an unread frame with the newest complete frame and
increments `preview.dropped_frames`. This is an intentional low-latency policy;
it avoids replaying stale video after a temporary stall. A queue size from one
to eight can be selected when every intermediate frame matters.

## Lifecycle and safety

`frame.faces` contains rectangles with **top-left** `x`/`y`, `width`, and
`height` in sensor pixels. The SDK converts the device's center-based boxes
once when decoding telemetry; `face.center` returns the original center.
Odd dimensions can produce half-pixel coordinates and boxes at image edges
can have negative origins. Draw them directly and let the canvas clip them;
do not subtract half the size again. Raw telemetry retains device coordinates.

`open_preview(timeout=10.0)` allows ten seconds for the start command, including
cold camera initialization. Set a positive timeout to override this deadline,
or `None` to disable it. This is separate from each `preview.read(timeout=...)`.

Only one preview session may be open per `WatcheRobot` connection. Closing the
context sends the configured stop policy:

- `hold` stops following and keeps the current head position.
- `recenter` stops following and requests the configured smooth return to the
  center position.

If the Application device channel disappears unexpectedly, the Runtime sends
`hold` to the device. A blocked reader is also released with
`WatcheRobotError`, so an Application does not hang indefinitely after a
disconnect.

Supported preview resolutions are `240x240`, `416x416`, and `640x480`.
The unified PTL firmware with face-preview support uses **640x480 only**;
pass `width=640, height=480` explicitly. Older PTL builds do not advertise
`face_tracking.preview.v1` and cannot provide simultaneous tracking and images.
`frame_stride` accepts one through three. Availability still depends on the
connected firmware advertising `face_tracking.preview.v1`; the SDK fails
closed when that capability is missing.

SDK Test Bench (`examples/sdk_media_lab`) exposes this API through **Start with
Preview**. It starts tracking and image delivery together; `start()` has no
preview flag. Use `open_preview(...)` instead of calling both start methods.

## Ownership boundary

Application code should not connect to the Watcher LAN address, preview UDP
port, or hardware WebSocket directly. Calling `open_preview()` sends a normal
SDK command through the authorized Application device channel. The Runtime
then delivers the reassembled preview frames back through that same channel.
When no managed Application is active, the Runtime can continue serving the
desktop diagnostic preview through its existing external channel.
