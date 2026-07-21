import socket

from watcherobot.transport import select_lan_bind_host


def test_auto_bind_host_skips_meta_vpn_benchmark_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("198.18.0.1", 0)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.168.31.99", 0)),
        ],
    )

    assert select_lan_bind_host("auto") == "192.168.31.99"


def test_auto_bind_host_falls_back_to_any_when_no_private_lan_exists(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("198.18.0.1", 0)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("100.64.0.3", 0)),
        ],
    )

    assert select_lan_bind_host("auto") == "0.0.0.0"


def test_explicit_bind_host_is_not_rewritten():
    assert select_lan_bind_host("10.0.0.25") == "10.0.0.25"
