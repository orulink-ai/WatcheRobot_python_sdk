"""Workspace integration guard; firmware source stays in its own repository."""
import os
from pathlib import Path
import pytest


def test_idle_profile_loads_after_sd_and_behavior_services():
    root = os.environ.get('WATCHER_FIRMWARE_ROOT')
    if not root:
        pytest.skip('Set WATCHER_FIRMWARE_ROOT for paired firmware integration')
    main = Path(root, 'firmware/s3/main/app_main.c').read_text(encoding='utf-8')
    serial = Path(root, 'firmware/s3/main/serial_resource_transfer.c').read_text(encoding='utf-8')
    boot = main[main.index('void app_main(void)'):]
    assert boot.index('boot_prepare_animation_assets();') < boot.index('behavior_state_init();')
    assert boot.index('behavior_state_init();') < boot.index('joy_idle_store_init()')
    assert 'joy_idle_store_init()' not in serial
