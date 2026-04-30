"""Tests for wonderwall DNS server resolution logic."""

import ipaddress
import socket
from unittest.mock import Mock, patch

from wonderwall.dns import _catch_all_other, _make_catch_all_a, _resolve_a, run_dns_server

INTERNAL_NET = ipaddress.ip_network("172.128.0.0/24")
FALLBACK = "1.2.3.4"


def _gai(ip):
    """Minimal socket.getaddrinfo return value for a single IPv4 address."""
    return [(socket.AF_INET, None, None, None, (ip, 0))]


class TestResolveA:
    def test_returns_internal_ip_when_in_subnet(self):
        with patch("socket.getaddrinfo", return_value=_gai("172.128.0.5")):
            assert _resolve_a("svc.internal", INTERNAL_NET, FALLBACK) == "172.128.0.5"

    def test_returns_fallback_when_external(self):
        with patch("socket.getaddrinfo", return_value=_gai("8.8.8.8")):
            assert _resolve_a("example.com", INTERNAL_NET, FALLBACK) == FALLBACK

    def test_returns_fallback_when_no_subnet_configured(self):
        with patch("socket.getaddrinfo", return_value=_gai("172.128.0.5")):
            assert _resolve_a("svc.internal", None, FALLBACK) == FALLBACK

    def test_returns_fallback_on_dns_error(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            assert _resolve_a("bad.host", INTERNAL_NET, FALLBACK) == FALLBACK

    def test_returns_first_matching_internal_from_multiple_results(self):
        results = _gai("8.8.8.8") + _gai("172.128.0.10")
        with patch("socket.getaddrinfo", return_value=results):
            assert _resolve_a("multi.host", INTERNAL_NET, FALLBACK) == "172.128.0.10"


class TestCatchAllA:
    def test_returns_a_record_with_fallback_ip(self):
        handler = _make_catch_all_a(None, FALLBACK)
        query = Mock()
        query.name = "example.com"
        result = handler(query)
        assert result._record_kwargs["data"] == FALLBACK

    def test_returns_a_record_with_internal_ip(self):
        net = ipaddress.ip_network("10.0.0.0/8")
        handler = _make_catch_all_a(net, FALLBACK)
        query = Mock()
        query.name = "svc.internal"
        with patch("socket.getaddrinfo", return_value=_gai("10.0.0.5")):
            result = handler(query)
        assert result._record_kwargs["data"] == "10.0.0.5"


class TestCatchAllOther:
    def test_returns_none(self):
        assert _catch_all_other(Mock()) is None


class TestRunDnsServer:
    def test_starts_without_internal_subnet(self, monkeypatch):
        monkeypatch.setenv("DNS_A_RECORD_IP", "1.2.3.4")
        monkeypatch.delenv("INTERNAL_SUBNET", raising=False)
        with patch("wonderwall.dns.DirectApplication") as mock_app:
            mock_app.return_value.run.return_value = None
            run_dns_server()
            mock_app.return_value.run.assert_called_once()

    def test_starts_with_internal_subnet(self, monkeypatch):
        monkeypatch.setenv("DNS_A_RECORD_IP", "1.2.3.4")
        monkeypatch.setenv("INTERNAL_SUBNET", "10.0.0.0/8")
        with patch("wonderwall.dns.DirectApplication") as mock_app:
            mock_app.return_value.run.return_value = None
            run_dns_server()
            mock_app.return_value.run.assert_called_once()
