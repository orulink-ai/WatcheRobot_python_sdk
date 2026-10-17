import os
from pathlib import Path

import pytest

from watcherobot.management.activity import ActivityBusy, ActivityRegistry


def test_active_job_blocks_sdk_switch_and_releases(tmp_path: Path):
    registry = ActivityRegistry(tmp_path)
    lease = registry.begin("maintenance", os.getpid())
    with pytest.raises(ActivityBusy), registry.switch():
        pass
    registry.end(lease)
    with registry.switch(), pytest.raises(ActivityBusy):
        registry.begin("maintenance", os.getpid())


def test_wrong_token_does_not_release_other_job(tmp_path: Path):
    registry = ActivityRegistry(tmp_path)
    lease = registry.begin("desktop_ota", os.getpid())
    with pytest.raises(ValueError):
        registry.end("wrong")
    with pytest.raises(ActivityBusy), registry.switch():
        pass
    registry.end(lease)
