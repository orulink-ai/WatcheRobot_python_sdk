"""Strict, transport-independent behavior document validation."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

ID = re.compile(r"[A-Za-z0-9_-]{1,23}\Z")


def validate_profile(value: Any) -> dict[str, Any]:
    fields = {"schema", "behaviors"}
    if isinstance(value, dict) and value.get("schema") == 3:
        fields.add("pool_percentages")
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("行为配置字段不完整或包含未知字段")
    if type(value["schema"]) is not int or value["schema"] not in (1, 2, 3):
        raise ValueError("不支持的配置版本")
    rows = value["behaviors"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 24:
        raise ValueError("请保留 1–24 个行为")
    seen = set()
    result = []
    for row in rows:
        fields = {"id", "name", "expression", "motion", "sound", "weight"}
        if value["schema"] >= 2:
            fields.add("group")
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("行为字段无效")
        if value["schema"] >= 2 and row["group"] not in ("standby", "feature"):
            raise ValueError("行为分组无效")
        for key in ("id", "expression", "motion", "sound"):
            item = row[key]
            if item is None and key in ("motion", "sound"):
                continue
            if not isinstance(item, str) or ID.fullmatch(item) is None:
                raise ValueError("素材标识无效")
        if row["id"] in seen:
            raise ValueError("行为标识重复")
        seen.add(row["id"])
        if not isinstance(row["name"], str) or not row["name"].strip() or len(row["name"].encode("utf-8")) > 96:
            raise ValueError("行为名称需要 1–96 字节")
        if type(row["weight"]) is not int or not 0 <= row["weight"] <= 1_000_000:
            raise ValueError("权重必须是 0–1000000 的整数")
        result.append(dict(row))
    if not sum(row["weight"] for row in result):
        raise ValueError("至少启用一个行为")
    output = {"schema": value["schema"], "behaviors": result}
    if value["schema"] == 3:
        pools = value["pool_percentages"]
        if (not isinstance(pools, dict) or set(pools) != {"standby", "feature"}
                or any(type(n) is not int or not 0 <= n <= 100 for n in pools.values())
                or sum(pools.values()) != 100):
            raise ValueError("两组概率须为 0–100 的整数，合计 100%")
        for group, share in pools.items():
            if share and not any(row["group"] == group and row["weight"] > 0 for row in result):
                raise ValueError("有抽中概率的分组至少需要一个启用行为")
        output["pool_percentages"] = dict(pools)
    return output


def percentages(profile: dict[str, Any]) -> list[float]:
    rows = validate_profile(profile)["behaviors"]
    if profile["schema"] >= 2:
        output = [0.0] * len(rows)
        for group in ("standby", "feature"):
            indices = [i for i, row in enumerate(rows) if row["group"] == group]
            if not sum(rows[i]["weight"] for i in indices):
                continue
            legacy_rows = [{key: val for key, val in rows[i].items() if key != "group"} for i in indices]
            for i, share in zip(indices, percentages({"schema": 1, "behaviors": legacy_rows})):
                output[i] = share
        return output
    total = sum(row["weight"] for row in rows)
    units = [row["weight"] * 10000 // total for row in rows]
    order = sorted(range(len(rows)), key=lambda i: -(rows[i]["weight"] * 10000 % total))
    for index in order[:10000 - sum(units)]:
        units[index] += 1
    return [unit / 100 for unit in units]


@dataclass(frozen=True)
class SaveSession:
    device_id: str
    revision: int

    def check(self, device_id: str, revision: int) -> None:
        if not self.device_id or device_id != self.device_id:
            raise ValueError("设备已变化，请重新读取后编辑")
        if revision != self.revision:
            raise ValueError("设备配置版本已变化，请重新读取")
