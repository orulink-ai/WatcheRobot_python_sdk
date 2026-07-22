import pytest

from watcherobot.transport import DISCOVERY_BIND_HOST, BackgroundTransport


@pytest.mark.parametrize("host", ["auto", "192.168.31.99"])
def test_discovery_binds_wildcard_ipv4_for_limited_broadcasts(host):
    transport = BackgroundTransport(discovery_port=37021, host=host)

    assert DISCOVERY_BIND_HOST == "0.0.0.0"
    assert transport._discovery_local_addr() == ("0.0.0.0", 37021)
