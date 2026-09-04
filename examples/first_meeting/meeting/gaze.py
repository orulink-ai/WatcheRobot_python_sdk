"""Gaze cues measured from the composited 10 fps source GIFs.

Directions use the robot's point of view: standby3 shifts toward screen-right
(robot-left); standby4 toward screen-left (robot-right). No assets are edited.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import Settings


@dataclass(frozen=True)
class MotionCue:
    at_ms: int
    pan: int
    duration_ms: int


@dataclass(frozen=True)
class GazePlan:
    animation: str
    duration_ms: int
    cues: tuple[MotionCue, ...]


def gaze_plan(settings: Settings, direction: str) -> GazePlan:
    center = settings.pan_center
    if direction == 'left':
        first = center + round((settings.pan_left - center) * 5 / 18)
        return GazePlan(settings.left_animation, 3600, (
            MotionCue(200, first, 200),
            MotionCue(1300, settings.pan_left, 700),
            MotionCue(2900, center, 500),
        ))
    if direction == 'right':
        return GazePlan(settings.right_animation, 2400, (
            MotionCue(100, settings.pan_right, 400),
            MotionCue(1700, center, 500),
        ))
    raise ValueError('direction must be left or right')


async def play_timeline(plan: GazePlan, move: Callable[[MotionCue], Awaitable[None]],
                        sleep: Callable[[float], Awaitable[None]],
                        clock: Callable[[], float], check_stop: Callable[[], None],
                        report: Callable[[str], None] | None = None) -> None:
    # Start from animation acceptance, not a browser timer. SDK 0.1.8 doesn't
    # expose an LCD presentation timestamp, so this is not a hardware frame lock.
    origin = clock()
    for cue in plan.cues:
        target = origin + cue.at_ms / 1000
        await sleep(max(0, target - clock()))
        check_stop()
        lag_ms = (clock() - target) * 1000
        if lag_ms > 150:
            raise RuntimeError(f'动作调度滞后 {lag_ms:.0f}ms，已停止，避免表情与转头错位')
        if report:
            report(f'{plan.animation} +{cue.at_ms}ms → PAN {cue.pan}° / {cue.duration_ms}ms；调度偏差 {lag_ms:.0f}ms')
        await move(cue)
    await sleep(max(0, origin + plan.duration_ms / 1000 - clock()))
