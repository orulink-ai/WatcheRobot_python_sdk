import socket
import sys

import pytest

from watcherobot.transport import DISCOVERY_BIND_HOST, BackgroundTransport


@pytest.mark.parametrize("host", ["auto", "192.168.31.99"])
def test_discovery_binds_wildcard_ipv4_for_limited_broadcasts(host):
    transport = BackgroundTransport(discovery_port=37021, host=host)

    assert DISCOVERY_BIND_HOST == "0.0.0.0"
    assert transport._discovery_local_addr() == ("0.0.0.0", 37021)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux UDP broadcast delivery semantics")
def test_linux_wildcard_socket_receives_limited_broadcast():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        receiver.bind((DISCOVERY_BIND_HOST, 0))
        receiver.settimeout(1)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        payload = b"watcherobot-discovery-broadcast"
        sender.sendto(payload, ("255.255.255.255", receiver.getsockname()[1]))

        received, _ = receiver.recvfrom(256)
        assert received == payload
    finally:
        sender.close()
        receiver.close()
