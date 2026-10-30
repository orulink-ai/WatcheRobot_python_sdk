import json

from watcherobot.distribution.cli import main


def test_sdk_does_not_expose_usb_writes_or_serial_operations():
    import pytest
    from watcherobot.firmware.operations import ACTIONS

    assert not {"flash", "plan", "ports"} & ACTIONS
    with pytest.raises(SystemExit):
        main(["firmware", "execute", "--action", "flash"])


def test_firmware_check_reports_validation_error_without_starting_daemon(
    tmp_path, capsys
):
    result = main(["firmware", "check", str(tmp_path / "missing.zip"), "--jsonl"])
    assert result == 2
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "firmware_package_invalid"
    assert str(tmp_path) not in events[-1]["message"]


def test_capabilities_does_not_advertise_unfinished_installation(capsys):
    assert main(["firmware", "capabilities", "--jsonl"]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["data"]["capabilities"] == [
        "firmware_package_check",
        "firmware_requirement_contract",
        "firmware_operations_v1",
    ]


def test_sdk_entrypoint_exposes_same_local_contract(capsys):
    from watcherobot.cli import main as sdk_main

    assert sdk_main(["firmware", "capabilities", "--jsonl"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["capabilities"] == [
        "firmware_package_check",
        "firmware_requirement_contract",
        "firmware_operations_v1",
    ]
