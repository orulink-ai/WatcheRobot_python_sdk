"""Transfer and play the bundled PCM WAV file."""

import asyncio
from pathlib import Path

from watcherobot.application import ApplicationContext


SAMPLE_AUDIO = Path(__file__).resolve().parents[1] / "assets" / "sample_speech.wav"


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        playback = await asyncio.to_thread(app.robot.audio.play_file, SAMPLE_AUDIO)
        await asyncio.to_thread(playback.wait, 30.0)
        app.logger.info("audio playback completed")


asyncio.run(main())
