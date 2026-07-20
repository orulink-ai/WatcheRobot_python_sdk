import json

from watcherobot.protocol import DISCOVERY_PROTOCOL_VERSION
from watcherobot.transport import DiscoveryProtocol


class FakeDatagramTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))


def test_discovery_replies_only_when_pairing_code_matches():
    protocol = DiscoveryProtocol(websocket_port=8766, pairing_code="123456")
    transport = FakeDatagramTransport()
    protocol.connection_made(transport)

    protocol.datagram_received(
        json.dumps(
            {
                "cmd": "SDK_DISCOVER",
                "service": "watcher-sdk",
                "protocol_version": DISCOVERY_PROTOCOL_VERSION,
                "device_id": "watcher-test",
                "pairing_code": "123456",
                "request_id": "1234567890ABCDEF",
            }
        ).encode(),
        ("192.168.1.8", 37021),
    )
    protocol.datagram_received(
        json.dumps(
            {
                "cmd": "SDK_DISCOVER",
                "service": "watcher-sdk",
                "protocol_version": DISCOVERY_PROTOCOL_VERSION,
                "device_id": "watcher-test",
                "pairing_code": "654321",
                "request_id": "1234567890ABCDEF",
            }
        ).encode(),
        ("192.168.1.9", 37021),
    )
    protocol.datagram_received(b'{"cmd":"OTHER"}', ("192.168.1.10", 37021))

    assert len(transport.sent) == 1
    payload, target = transport.sent[0]
    assert target == ("192.168.1.8", 37021)
    assert json.loads(payload) == {
        "cmd": "ANNOUNCE",
        "service": "watcher-sdk",
        "protocol_version": DISCOVERY_PROTOCOL_VERSION,
        "port": 8766,
        "server": "watcherobot-python-sdk",
        "pairing_code": "123456",
        "request_id": "1234567890ABCDEF",
    }

