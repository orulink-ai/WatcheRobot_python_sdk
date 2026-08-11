"""Record five seconds from the robot microphone."""

import asyncio
from pathlib import Path

from watcherobot.application import ApplicationContext


OUTPUT_FILE = Path(__file__).resolve().parent / "artifacts" / "microphone.wav"


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        recording = await asyncio.to_thread(
            app.robot.microphone.record_pcm,
            duration=5.0,
            timeout=8.0,
        )
        saved = recording.save(OUTPUT_FILE)
        app.logger.info(
            "recording saved: %s duration=%.3fs dropped_frames=%d decode_failures=%d",
            saved,
            recording.duration_seconds,
            recording.dropped_frames,
            recording.decode_failures,
        )


asyncio.run(main())
