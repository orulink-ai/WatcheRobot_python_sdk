"""Truthful display feedback through the injected, negotiated SDK only."""
import asyncio

from async_utils import drain_owned


class BodyFeedback:
    def __init__(self, robot):
        self.robot = robot
        self.supported = bool(getattr(robot, 'supports', lambda _: False)('expression.runtime.v3'))
        self.status = 'idle' if self.supported else 'unsupported'
        self.lock = asyncio.Lock()

    async def _call(self, callback, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await drain_owned(task)
            raise

    async def show(self, preset):
        if not self.supported:
            return
        if preset not in {'thinking', 'speaking'}:
            raise ValueError('unsupported body feedback')
        async with self.lock:
            if self.status in {'thinking', 'speaking'}:
                await self._call(self.robot.expression_runtime.update, preset=preset, transition_ms=180)
            else:
                await self._call(self.robot.expression_runtime.start, preset, transition_ms=180)
            self.status = preset  # The label follows the ACK, never an optimistic send.

    async def close(self):
        if not self.supported:
            return
        async with self.lock:
            # Always release after owner tasks drain, even when start's ACK was
            # lost or cancellation raced the firmware applying a new preset.
            await self._call(self.robot.expression_runtime.stop)
            self.status = 'released'


class SuppressedBodyFeedback(BodyFeedback):
    """Keep visible expressions black, without claiming renderer shutdown.

    The public stop command resumes firmware animations. Hold display ownership
    for the Application lifetime instead; no speaking/thinking preset updates.
    """

    async def disable(self):
        if not self.supported:
            raise RuntimeError('设备没有协商 expression.runtime.v3，无法确认黑屏接管')
        async with self.lock:
            if self.status != 'suppressed':
                await self._call(self.robot.expression_runtime.start, 'standby',
                                 color='#000000', auto_blink=False, transition_ms=0)
                self.status = 'suppressed'

    async def show(self, preset):
        return  # No automatic facial feedback, even during tasks/playback.

    async def close(self):
        return  # Voice stop is not permission to bring expressions back.

    async def release(self):
        await super().close()  # Only Application teardown releases the display.
