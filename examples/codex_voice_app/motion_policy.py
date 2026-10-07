"""WatcheRobot hardware travel, separate from the model's tool contract.

STM32 User/App/app.c: servo2 (pan) is 30..150, servo1 (tilt) is
100..130. This is a firmware profile, NOT a live limit/encoder query.
Do not widen it to the protocol's nominal 0..180 range.
"""
from dataclasses import dataclass
import math


POSITIONS = ('pan_min', 'center', 'pan_max')
PACES = ('gentle', 'natural')


@dataclass(frozen=True)
class MotionEnvelope:
    pan_min_deg: int = 30
    center_deg: int = 90
    pan_max_deg: int = 150
    tilt_deg: int = 120
    max_speed_deg_s: int = 60

    def __post_init__(self):
        values = (self.pan_min_deg, self.center_deg, self.pan_max_deg,
                  self.tilt_deg, self.max_speed_deg_s)
        if (any(type(value) is not int for value in values)
                or not 30 <= self.pan_min_deg < self.center_deg < self.pan_max_deg <= 150
                or not 100 <= self.tilt_deg <= 130
                or not 1 <= self.max_speed_deg_s <= 60):
            raise ValueError('运动范围必须在已知固件行程内，且中心、俯仰与速度有效')

    def angle(self, position):
        if position not in POSITIONS:
            raise ValueError('不支持的观察视角')
        return dict(pan_min=self.pan_min_deg, center=self.center_deg,
                    pan_max=self.pan_max_deg)[position]

    def duration_ms(self, angle, pace='natural'):
        if type(angle) is not int or not self.pan_min_deg <= angle <= self.pan_max_deg:
            raise ValueError('角度超出设备运动范围')
        if pace not in PACES:
            raise ValueError('不支持的转头节奏')
        # No current-angle API exists. Budget for departure from either endpoint,
        # plus a 2x easing allowance. Never reuse a stale commanded position as
        # feedback: firmware idle behavior or manual handling can move the head.
        distance = max(angle - self.pan_min_deg, self.pan_max_deg - angle,
                       self.tilt_deg - 100, 130 - self.tilt_deg)
        speed = min(self.max_speed_deg_s, 20) if pace == 'gentle' else self.max_speed_deg_s
        return max(1500, math.ceil(2000 * distance / speed))

    def snapshot(self):
        return dict(panMinDeg=self.pan_min_deg, centerDeg=self.center_deg,
                    panMaxDeg=self.pan_max_deg, tiltDeg=self.tilt_deg,
                    speedBudgetDegS=self.max_speed_deg_s, defaultPace='natural',
                    paces=list(PACES),
                    limitSource='firmware-profile', directionCalibrated=False)
