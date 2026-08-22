"""``reminder.schedule`` 纯逻辑的单元测试：闹钟触发时间计算。"""

from datetime import datetime, time

import pytest

from reminder.schedule import Alarm, next_fire_time, next_fires, seconds_until


def make_alarm(hour: int = 19, minute: int = 0, days=(), text: str = "到点啦！") -> Alarm:
    return Alarm(id="a1", time=time(hour, minute), text=text, days=days)


class TestNextFireTime:
    def test_later_today(self) -> None:
        alarm = make_alarm(19, 0)
        now = datetime(2025, 1, 1, 8, 30, 15)
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 1, 19, 0, 0)

    def test_already_passed_rolls_to_tomorrow(self) -> None:
        alarm = make_alarm(19, 0)
        now = datetime(2025, 1, 1, 23, 59, 59)
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 2, 19, 0, 0)

    def test_exact_time_rolls_to_tomorrow(self) -> None:
        """恰好等于触发时刻必须推到明天，避免同秒重复触发。"""
        alarm = make_alarm(19, 0)
        now = datetime(2025, 1, 1, 19, 0, 0)
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 2, 19, 0, 0)

    def test_microseconds_not_carried(self) -> None:
        alarm = make_alarm(19, 0)
        now = datetime(2025, 1, 1, 18, 0, 0, 999_999)
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 1, 19, 0, 0)


class TestNextFireTimeWeekdays:
    def test_weekdays_skips_weekend(self) -> None:
        """2025-01-04 是周六：周一至周五闹钟的下一次是周一。"""
        alarm = make_alarm(9, 0, days=(0, 1, 2, 3, 4))
        now = datetime(2025, 1, 4, 10, 0, 0)  # 周六
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 6, 9, 0, 0)

    def test_weekends_picks_saturday(self) -> None:
        alarm = make_alarm(12, 0, days=(5, 6))
        now = datetime(2025, 1, 3, 20, 0, 0)  # 周五
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 4, 12, 0, 0)

    def test_single_day_fires_today_before_time(self) -> None:
        """2025-01-05 是周日，只在周日响的闹钟：今天没到点就今天响。"""
        alarm = make_alarm(8, 0, days=(6,))
        now = datetime(2025, 1, 5, 7, 0, 0)
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 5, 8, 0, 0)

    def test_single_day_rolls_to_next_week_after_time(self) -> None:
        alarm = make_alarm(8, 0, days=(6,))
        now = datetime(2025, 1, 5, 9, 0, 0)  # 周日已过 8 点 → 下周日
        assert next_fire_time(alarm, now=now) == datetime(2025, 1, 12, 8, 0, 0)


class TestNextFires:
    def test_sorted_by_fire_time_and_groups(self) -> None:
        morning = make_alarm(9, 0, text="早")
        evening = make_alarm(19, 0, text="晚")
        same = make_alarm(19, 0, text="晚二")
        now = datetime(2025, 1, 1, 12, 0, 0)
        fires = next_fires([evening, morning, same], now=now)
        assert [(t, a.text) for t, a in fires] == [
            (datetime(2025, 1, 1, 19, 0, 0), "晚"),
            (datetime(2025, 1, 1, 19, 0, 0), "晚二"),
            (datetime(2025, 1, 2, 9, 0, 0), "早"),
        ]

    def test_empty(self) -> None:
        assert next_fires([], now=datetime(2025, 1, 1)) == []


class TestSecondsUntil:
    def test_positive(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0)
        when = datetime(2025, 1, 1, 19, 0, 0)
        assert seconds_until(when, now=now) == pytest.approx(7 * 3600)

    def test_past_clamps_to_zero(self) -> None:
        now = datetime(2025, 1, 2, 12, 0, 0)
        when = datetime(2025, 1, 1, 19, 0, 0)
        assert seconds_until(when, now=now) == 0.0