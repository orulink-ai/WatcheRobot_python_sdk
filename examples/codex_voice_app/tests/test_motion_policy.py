"""The Application uses a bounded hardware profile, never arbitrary model angles."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


def test_hardware_profile_uses_complete_firmware_travel_and_valid_tilt():
    from motion_policy import MotionEnvelope
    profile = MotionEnvelope()
    assert profile.angle('pan_min') == 30
    assert profile.angle('center') == 90
    assert profile.angle('pan_max') == 150
    assert profile.tilt_deg == 120
    assert profile.snapshot()['limitSource'] == 'firmware-profile'
    assert profile.snapshot()['directionCalibrated'] is False


def test_longer_motion_has_longer_duration_even_when_start_angle_is_unknown():
    from motion_policy import MotionEnvelope
    profile = MotionEnvelope()
    assert profile.duration_ms(90) == 2000
    assert profile.duration_ms(30) == profile.duration_ms(150) == 4000
    assert profile.duration_ms(90, 'gentle') == 6000
    assert profile.duration_ms(30, 'gentle') == 12000
    # Worst possible departure is the opposite firmware endpoint. A factor of
    # two provides a conservative easing allowance, not just an average speed.
    for angle in range(30, 151):
        distance = max(abs(angle - 30), abs(angle - 150))
        assert profile.duration_ms(angle) / 1000 >= 2 * distance / 60
    with pytest.raises(ValueError):
        profile.duration_ms(0)
    with pytest.raises(ValueError):
        profile.angle('pan80')
    with pytest.raises(ValueError):
        profile.duration_ms(90, 'unlimited')


@pytest.mark.parametrize('kwargs', [
    {'pan_min_deg': 0}, {'pan_max_deg': 180}, {'center_deg': 30},
    {'tilt_deg': 90}, {'max_speed_deg_s': 0}, {'max_speed_deg_s': True},
    {'max_speed_deg_s': 61},
    {'pan_min_deg': 30.5}, {'pan_max_deg': 90},
])
def test_profile_cannot_expand_past_known_hardware_or_accept_invalid_values(kwargs):
    from motion_policy import MotionEnvelope
    with pytest.raises(ValueError):
        MotionEnvelope(**kwargs)
