from __future__ import annotations

import sys
from types import SimpleNamespace

from watcherobot.runtime import frozen_entry


def test_maintenance_esptool_serial_failure_returns_without_raising(
    monkeypatch, capsys
) -> None:
    def fail(_args: list[str]) -> None:
        raise RuntimeError("Could not open COM29, the port is busy")

    monkeypatch.setitem(sys.modules, "esptool", SimpleNamespace(main=fail))

    assert frozen_entry.main(["--maintenance-esptool", "write-flash"]) == 2
    assert "Could not open COM29" in capsys.readouterr().err


def test_maintenance_esptool_preserves_normal_exit_code(monkeypatch) -> None:
    def exit_with_error(_args: list[str]) -> None:
        raise SystemExit(3)

    monkeypatch.setitem(sys.modules, "esptool", SimpleNamespace(main=exit_with_error))

    assert frozen_entry.main(["--maintenance-esptool"]) == 3
