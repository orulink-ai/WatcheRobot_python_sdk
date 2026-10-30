from dataclasses import replace

from watcherobot.firmware.status import firmware_status
from tests.distribution.test_firmware_transfer import artifact


def test_wifi_version_is_current_but_does_not_invent_firmware_identity(tmp_path):
    release, _ = artifact(tmp_path)
    result = firmware_status(
        {"firmware_version": "v0.5.7", "observed_at": 100}, [release], now=101
    )
    assert result["version"] == "v0.5.7"
    assert result["evidence"] == "wifi"
    assert result["firmware_id"] is None
    assert result["ota_allowed"] is False


def test_history_is_not_a_live_official_firmware_readback(tmp_path):
    release, _ = artifact(tmp_path)
    record = {
        "status": "succeeded",
        "release": {"firmware_id": release.firmware_id, "version": release.version},
        "port": "USB-TEST",
    }
    result = firmware_status(None, [release], last_write=record, now=100)
    assert result["evidence"] == "last_write"
    assert result["version"] == release.version
    assert result["runtime_version_verified"] is False
    assert result["ota_allowed"] is False


def test_ota_requires_reviewed_official_origin_and_fresh_matching_identity(tmp_path):
    release, _ = artifact(tmp_path)
    official = replace(
        release,
        repo_id="Orulink/WatcherRobot-Firmware-system",
        publisher_kind="official",
        ota_supported=True,
    )
    observed = {
        "firmware_id": release.firmware_id,
        "firmware_version": release.version,
        "observed_at": 100,
    }
    assert firmware_status(observed, [official], now=101)["ota_allowed"] is True
    assert (
        firmware_status(
            observed, [replace(official, repo_id="someone/official-firmware")], now=101
        )["ota_allowed"]
        is False
    )
    assert firmware_status(observed, [official], now=200)["ota_allowed"] is False
    assert (
        firmware_status(observed, [replace(official, withdrawn=True)], now=101)[
            "ota_allowed"
        ]
        is False
    )


def test_third_party_and_failed_write_never_enable_ota(tmp_path):
    release, _ = artifact(tmp_path)
    observed = {
        "firmware_id": release.firmware_id,
        "firmware_version": release.version,
        "observed_at": 100,
    }
    assert firmware_status(observed, [release], now=101)["ota_allowed"] is False
    assert (
        firmware_status(
            None, [release], last_write={"status": "failed", "release": {}}, now=101
        )["evidence"]
        == "unknown"
    )
