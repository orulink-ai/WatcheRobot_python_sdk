"""闹钟数据存储：JSON 持久化 + 增删改查 + 校验，写盘采用原子替换。"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any

from reminder.schedule import ALL_DAYS, Alarm


class AlarmValidationError(ValueError):
    """闹钟字段不合法时抛出（网页 API 会转成 400 响应）。"""


def parse_time(value: str) -> datetime.time:
    """接受 ``HH:MM`` 字符串，返回 datetime.time。"""
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return datetime.time(int(hour_text), int(minute_text))
    except (ValueError, AttributeError) as exc:
        raise AlarmValidationError(f"时间格式应为 HH:MM：{value!r}") from exc


def validate_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AlarmValidationError("播报内容不能为空")
    if len(text) > 200:
        raise AlarmValidationError("播报内容不能超过 200 字")
    return text


def normalize_days(value: Any) -> tuple[int, ...]:
    """把多种输入（空、逗号字符串、列表/元组）归一为 0-6 去重元组。"""
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        parts = [int(part) for part in value.split(",") if part.strip() != ""]
    else:
        try:
            parts = [int(day) for day in value]
        except (TypeError, ValueError) as exc:
            raise AlarmValidationError("重复规则必须是 0（周一）到 6（周日）的整数列表") from exc
    for day in parts:
        if not 0 <= day <= 6:
            raise AlarmValidationError("重复规则必须是 0（周一）到 6（周日）的整数")
    return tuple(dict.fromkeys(parts))


def to_dict(alarm: Alarm) -> dict[str, Any]:
    """闹钟的 JSON 友好表示。"""
    return {
        "id": alarm.id,
        "time": alarm.time.strftime("%H:%M"),
        "text": alarm.text,
        "days": list(alarm.days),
        "enabled": alarm.enabled,
        "daily": alarm.repeats_daily,
    }


def from_dict(item: dict[str, Any]) -> Alarm:
    """从 JSON 字典恢复闹钟（读取时宽容处理历史/残缺字段）。"""
    raw_id = item.get("id") or uuid.uuid4().hex[:8]
    raw_days = item.get("days") or ()
    return Alarm(
        id=str(raw_id),
        time=parse_time(str(item.get("time") or "")) if item.get("time") else datetime.time(7, 0),
        text=validate_text(item.get("text", "该起床啦！")),
        days=normalize_days(raw_days),
        enabled=bool(item.get("enabled", True)),
    )


class AlarmStore:
    """闹钟的 JSON 文件存储；写入用临时文件 + 原子替换，防止半写损坏。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._alarms: list[Alarm] = []

    # -- 持久化 --
    def load(self) -> "AlarmStore":
        if not self.path.exists():
            self._alarms = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            items = raw["alarms"] if isinstance(raw, dict) else raw
            self._alarms = [from_dict(item) for item in items]
        except (OSError, ValueError, TypeError, KeyError):
            backup = self.path.with_name(self.path.name + ".corrupt.bak")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            self._alarms = []
        return self

    def save(self) -> None:
        payload = {"version": 1, "alarms": [to_dict(alarm) for alarm in self._alarms]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- 查询 --
    def all(self) -> list[Alarm]:
        return sorted(self._alarms, key=lambda alarm: (alarm.time, alarm.id))

    def all_enabled(self) -> list[Alarm]:
        return [alarm for alarm in self.all() if alarm.enabled]

    def get(self, alarm_id: str) -> Alarm | None:
        return next((alarm for alarm in self._alarms if alarm.id == alarm_id), None)

    # -- 增删改 --
    def add(
        self,
        *,
        time: str | datetime.time,
        text: str,
        days: Any = (),
        enabled: bool = True,
    ) -> Alarm:
        alarm = Alarm(
            id=uuid.uuid4().hex[:8],
            time=parse_time(time) if isinstance(time, str) else time,
            text=validate_text(text),
            days=normalize_days(days),
            enabled=bool(enabled),
        )
        self._alarms.append(alarm)
        self.save()
        return alarm

    def remove(self, alarm_id: str) -> bool:
        before = len(self._alarms)
        self._alarms = [alarm for alarm in self._alarms if alarm.id != alarm_id]
        changed = len(self._alarms) != before
        if changed:
            self.save()
        return changed

    def update(self, alarm_id: str, **fields: Any) -> Alarm | None:
        alarm = self.get(alarm_id)
        if alarm is None:
            return None
        new_time = fields.get("time", alarm.time)
        new_text = fields.get("text", alarm.text)
        new_days = fields.get("days", alarm.days)
        new_enabled = fields.get("enabled", alarm.enabled)
        updated = Alarm(
            id=alarm.id,
            time=parse_time(new_time) if isinstance(new_time, str) else new_time,
            text=validate_text(new_text),
            days=normalize_days(new_days),
            enabled=bool(new_enabled),
        )
        self._alarms = [updated if item.id == alarm_id else item for item in self._alarms]
        self.save()
        return updated


__all__ = [
    "ALL_DAYS",
    "AlarmStore",
    "AlarmValidationError",
    "from_dict",
    "normalize_days",
    "parse_time",
    "to_dict",
    "validate_text",
]