"""闹钟触发调度的纯逻辑：与 SDK 无关，便于单元测试。

时间一律使用本机本地时间（naive datetime）；夏令时切换时按墙钟推进。
星期采用 Python ``datetime.weekday()`` 约定：周一=0 … 周日=6。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Iterable

ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class Alarm:
    """一条闹钟：每天或指定星期几的 ``time`` 时刻触发，播报 ``text``。

    ``days`` 为空元组表示每天；否则为 0（周一）到 6（周日）的去重集合。
    """

    id: str
    time: datetime.time
    text: str
    days: tuple[int, ...] = ()
    enabled: bool = True

    @property
    def repeats_daily(self) -> bool:
        return not self.days or set(self.days) == set(ALL_DAYS)


def _weekdays(alarm: Alarm) -> tuple[int, ...]:
    days = tuple(dict.fromkeys(alarm.days))  # 去重且保序
    if not days:
        return ALL_DAYS
    if any(not isinstance(day, int) or not 0 <= day <= 6 for day in days):
        raise ValueError("days 必须是 0（周一）到 6（周日）的整数")
    return days


def next_fire_time(alarm: Alarm, *, now: datetime.datetime) -> datetime.datetime:
    """返回 ``now`` 之后（严格大于）符合重复规则的最近一次触发时间。"""
    days = _weekdays(alarm)
    for offset in range(8):  # 最多 7 天后的同一天，必然命中
        candidate_date = now.date() + datetime.timedelta(days=offset)
        if candidate_date.weekday() not in days:
            continue
        candidate = datetime.datetime.combine(candidate_date, alarm.time)
        if candidate > now:
            return candidate
    raise ValueError("alarm has no upcoming fire time")  # days 非空时不会发生


def seconds_until(when: datetime.datetime, *, now: datetime.datetime) -> float:
    """距 ``when`` 的剩余秒数，最小为 0（用于 asyncio.sleep 的等待时长）。"""
    return max(0.0, (when - now).total_seconds())


def next_fires(
    alarms: Iterable[Alarm],
    *,
    now: datetime.datetime,
) -> list[tuple[datetime.datetime, Alarm]]:
    """返回每条闹钟的下一次触发时间，按触发时间升序排列。"""
    fires = [(next_fire_time(alarm, now=now), alarm) for alarm in alarms]
    fires.sort(key=lambda item: item[0])
    return fires