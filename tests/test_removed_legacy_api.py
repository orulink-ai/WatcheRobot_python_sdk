from __future__ import annotations

import importlib.util

import watcherobot
from watcherobot.robot import WatcheRobot


def test_legacy_direct_connection_api_is_not_published() -> None:
    assert not hasattr(watcherobot, "WatcheRobot")
    assert not hasattr(WatcheRobot, "connect")
    assert importlib.util.find_spec("watcherobot.transport") is None
