"""Consume 150 low-latency face-tracking preview frames."""

import asyncio
from pathlib import Path

from watcherobot.application import ApplicationContext


OUTPUT_FILE = Path(__file__).resolve().parent / "artifacts" / "latest.jpg"
FRAME_LIMIT = 150


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        preview = await asyncio.to_thread(
            app.robot.face_tracking.open_preview,
            width=416,
            height=416,
            frame_stride=1,
            stop_policy="hold",
        )
        latest = None
        frame_count = 0
        async with preview:
            async for frame in preview:
                latest = frame
                frame_count += 1
                app.logger.info(
                    "seq=%d faces=%d age=%dms inference=%s dropped=%d",
                    frame.sequence,
                    len(frame.faces),
                    frame.telemetry.age_ms,
                    frame.telemetry.inference_ms,
                    preview.dropped_frames,
                )
                if frame_count >= FRAME_LIMIT:
                    break

        if latest is not None:
            saved = await asyncio.to_thread(latest.save, OUTPUT_FILE)
            app.logger.info("latest preview frame saved: %s", saved)


asyncio.run(main())
