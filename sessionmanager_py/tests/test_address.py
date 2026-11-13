import pytest

from framecore_session import parse_tcp_address_link


def test_parse_multiple_tcp_endpoints():
    endpoints = parse_tcp_address_link(
        "TCP:10.0.0.1:1089; TCP:fm.example.com:1090;"
    )
    assert [(item.protocol, item.host, item.port) for item in endpoints] == [
        ("TCP", "10.0.0.1", 1089),
        ("TCP", "fm.example.com", 1090),
    ]


def test_parse_mixed_link_keeps_tcp_and_supports_ipv6():
    endpoints = parse_tcp_address_link(
        "UDP:10.0.0.1:1089;V6TCP:[::1]:1090;TCP:127.0.0.1:1091"
    )
    assert [(item.host, item.port) for item in endpoints] == [
        ("::1", 1090),
        ("127.0.0.1", 1091),
    ]


@pytest.mark.parametrize("address", [
    "", "TCP:127.0.0.1", "TCP::1089", "TCP:127.0.0.1:0", "UDP:127.0.0.1:1089",
])
def test_reject_invalid_or_tcp_free_address_link(address):
    with pytest.raises(ValueError):
        parse_tcp_address_link(address)
