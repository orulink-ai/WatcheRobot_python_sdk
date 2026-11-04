"""Authored silent waking; eyelids interpolate while the same motion job runs."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class WakePose:
    pan_offset: int
    tilt: int
    duration_ms: int
    left_upper: int
    right_upper: int
    lower: int
    mouth_opacity: float
    gaze_y: float = 0.0
    hold_ms: int = 0

    def lids(self, *, auto_blink=False, transition_ms=None):
        return dict(left_upper_lid_y=self.left_upper, right_upper_lid_y=self.right_upper,
                    left_lower_lid_y=self.lower, right_lower_lid_y=self.lower,
                    left_upper_lid_rotation_deg=0, right_upper_lid_rotation_deg=0,
                    left_lower_lid_rotation_deg=0, right_lower_lid_rotation_deg=0,
                    auto_blink=auto_blink, mouth_opacity=self.mouth_opacity, gaze_y=self.gaze_y,
                    transition_ms=self.duration_ms if transition_ms is None else transition_ms)


# Two openings in total: one sleepy relapse, then a continuous final lift.
# Mouth stays hidden until the final lift; authored duration stays eight seconds.
WAKE_POSES = (
    WakePose(0, 100, 600, -32, -32, 32, 0.0, 1.0),
    WakePose(-1, 103, 2000, -49, -43, 55, 0.0, .95, hold_ms=350),
    WakePose(0, 100, 1000, -32, -32, 32, 0.0, 1.0, hold_ms=350),
    WakePose(2, 110, 2000, -65, -59, 72, 0.7, .25),
    WakePose(0, 113, 1700, -80, -80, 80, 1.0),
)


async def awaken(robot, pan, check_stop, pause):
    for index, pose in enumerate(WAKE_POSES):
        check_stop()
        await robot.eyelids(**pose.lids(transition_ms=0 if index == 0 else None))
        check_stop()
        started = time.monotonic()
        await robot.move(max(70, min(110, pan + pose.pan_offset)), pose.tilt, pose.duration_ms)
        await pause(max(0, pose.duration_ms / 1000 - (time.monotonic() - started)))
        check_stop()
        if pose.hold_ms:
            await pause(pose.hold_ms / 1000)
            check_stop()
    await robot.eyelids(**WAKE_POSES[-1].lids(auto_blink=True, transition_ms=0))
    await pause(0.12)
