"""``reminder.alarms`` 的单元测试：JSON 存储、CRUD 与校验。"""

import json
from pathlib import Path

import pytest

from reminder.alarms import (
    AlarmStore,
    AlarmValidationError,
    normalize_days,
    parse_time,
    validate_text,
)
from reminder.schedule import Alarm


class TestValidators:
    def test_parse_time_ok(self) -> None:
        assert str(parse_time("19:05")) == "19:05:00"

    @pytest.mark.parametrize("bad", ["", "7", "25:00", "ab:cd"])
    def test_parse_time_rejects(self, bad: str) -> None:
        with pytest.raises(AlarmValidationError):
            parse_time(bad)

    def test_text_requires_non_empty(self) -> None:
        with pytest.raises(AlarmValidationError):
            validate_text("   ")
        assert validate_text(" 到点啦 ") == "到点啦"

    @pytest.mark.parametrize("value,expected", [
        ((), ()),
        ("", ()),
        ([0, 1, 2], (0, 1, 2)),
        ("0,2,4", (0, 2, 4)),
        ([6, 6, 0], (6, 0)),  # 去重保序
    ])
    def test_normalize_days(self, value, expected) -> None:
        assert normalize_days(value) == expected

    def test_normalize_days_rejects_out_of_range(self) -> None:
        with pytest.raises(AlarmValidationError):
            normalize_days([7])


class TestStoreLifecycle:
    def test_add_list_update_remove_roundtrip(self, tmp_path: Path) -> None:
        store = AlarmStore(tmp_path / "alarms.json").load()
        alarm = store.add(time="07:30", text="起床啦", days=[0, 1, 2, 3, 4])
        assert alarm.days == (0, 1, 2, 3, 4)

        listed = store.all()
        assert len(listed) == 1
        assert str(listed[0].time) == "07:30:00"
        assert listed[0].text == "起床啦"

        updated = store.update(alarm.id, enabled=False)
        assert updated is not None and updated.enabled is False
        assert store.all_enabled() == []

        assert store.remove(alarm.id) is True
        assert store.remove(alarm.id) is False
        assert store.all() == []

    def test_persistence_across_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "alarms.json"
        store = AlarmStore(path).load()
        store.add(time="19:00", text="晚上好", days=[5, 6])

        reloaded = AlarmStore(path).load()
        alarms = reloaded.all()
        assert len(alarms) == 1
        assert alarms[0].id != ""
        assert alarms[0].days == (5, 6)
        assert alarms[0].text == "晚上好"

    def test_save_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        store = AlarmStore(tmp_path / "alarms.json").load()
        store.add(time="08:00", text="早")
        assert not (tmp_path / "alarms.json.tmp").exists()
        assert (tmp_path / "alarms.json").exists()

    def test_corrupt_file_resets_and_backs_up(self, tmp_path: Path) -> None:
        path = tmp_path / "alarms.json"
        path.write_text("{ not valid json !!!", encoding="utf-8")
        store = AlarmStore(path).load()
        assert store.all() == []
        assert (tmp_path / "alarms.json.corrupt.bak").exists()

    def test_missing_file_starts_empty(self, tmp_path: Path) -> None:
        store = AlarmStore(tmp_path / "alarms.json").load()
        assert store.all() == []

    def test_validation_errors_do_not_persist(self, tmp_path: Path) -> None:
        store = AlarmStore(tmp_path / "alarms.json").load()
        with pytest.raises(AlarmValidationError):
            store.add(time="24:99", text="坏时间")
        with pytest.raises(AlarmValidationError):
            store.add(time="08:00", text="")
        assert store.all() == []

    def test_from_dict_tolerates_legacy_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "alarms.json"
        path.write_text(
            json.dumps({"version": 1, "alarms": [{"id": "abc1", "time": "09:00", "text": "hi", "days": [0], "enabled": False}]}),
            encoding="utf-8",
        )
        store = AlarmStore(path).load()
        alarm = store.get("abc1")
        assert alarm is not None
        assert alarm.enabled is False
        assert alarm.days == (0,)


class TestAlarmModel:
    def test_repeats_daily(self) -> None:
        assert Alarm(id="x", time=parse_time("08:00"), text="t").repeats_daily is True
        assert Alarm(id="x", time=parse_time("08:00"), text="t", days=(1,)).repeats_daily is False