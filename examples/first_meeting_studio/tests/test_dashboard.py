import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.dashboard import open_listener


def test_dashboard_reuses_configured_port_after_restart():
    listener = open_listener(0)
    port = listener.getsockname()[1]
    listener.close()
    restarted = open_listener(port)
    try:
        assert restarted.getsockname() == ("127.0.0.1", port)
    finally:
        restarted.close()


def test_occupied_port_fails_instead_of_silently_changing_url():
    listener = open_listener(0)
    try:
        with pytest.raises(OSError):
            open_listener(listener.getsockname()[1])
    finally:
        listener.close()
