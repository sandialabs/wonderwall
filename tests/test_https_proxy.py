"""Tests for wonderwall TLS SNI proxy (wonderwall/proxy.py)."""

import asyncio
import re
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import wonderwall.https_proxy as proxy_module
from wonderwall.https_proxy import _parse_allowed_hosts, _wildcard_to_regex, extract_sni, handle_tls, relay
from tests.helpers import build_client_hello


def _connect_and_relay(port: int, data: bytes, timeout: float = 3.0) -> bytes:
    """Connect to port, send data, signal EOF, return everything received."""
    with socket.socket() as s:
        s.settimeout(timeout)
        s.connect(("127.0.0.1", port))
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        received = b""
        try:
            while chunk := s.recv(4096):
                received += chunk
        except socket.timeout:
            pass
        return received


# ─────────────────────────────────────────────
# extract_sni
# ─────────────────────────────────────────────


class TestExtractSni:
    def test_returns_sni_hostname(self):
        assert extract_sni(build_client_hello("example.com")) == "example.com"

    def test_returns_subdomain(self):
        assert extract_sni(build_client_hello("api.internal.company.com")) == "api.internal.company.com"

    def test_returns_long_hostname(self):
        hostname = "a" * 200 + ".example.com"
        assert extract_sni(build_client_hello(hostname)) == hostname

    def test_returns_none_for_empty_bytes(self):
        assert extract_sni(b"") is None

    def test_returns_none_for_short_data(self):
        assert extract_sni(b"\x16\x03\x01") is None

    def test_returns_none_if_not_tls_record(self):
        data = build_client_hello("example.com")
        assert extract_sni(b"\x17" + data[1:]) is None  # content type != 0x16

    def test_returns_none_if_not_client_hello(self):
        data = build_client_hello("example.com")
        # Byte 5 is the handshake type; flip it to ServerHello (0x02)
        assert extract_sni(data[:5] + b"\x02" + data[6:]) is None

    def test_returns_none_when_no_extensions(self):
        assert extract_sni(build_client_hello(sni=None, with_extensions=False)) is None

    def test_returns_none_when_extensions_present_but_no_sni(self):
        # Empty extensions list — no SNI extension
        assert extract_sni(build_client_hello(sni=None, with_extensions=True)) is None

    def test_returns_none_for_all_zeros(self):
        assert extract_sni(b"\x00" * 100) is None

    def test_returns_none_for_random_garbage(self):
        assert extract_sni(b"\xff" * 200) is None


# ─────────────────────────────────────────────
# relay
# ─────────────────────────────────────────────


class TestRelay:
    def test_forwards_data_to_writer(self):
        async def _test():
            reader = asyncio.StreamReader()
            reader.feed_data(b"hello world")
            reader.feed_eof()
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            await relay(reader, writer)
            writer.write.assert_called_once_with(b"hello world")
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_forwards_multiple_chunks(self):
        async def _test():
            reader = asyncio.StreamReader()
            reader.feed_data(b"first ")
            reader.feed_data(b"second")
            reader.feed_eof()
            received = []
            writer = MagicMock()
            writer.write = MagicMock(side_effect=received.append)
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            await relay(reader, writer)
            assert b"".join(received) == b"first second"
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_closes_writer_on_eof_with_no_data(self):
        async def _test():
            reader = asyncio.StreamReader()
            reader.feed_eof()
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            await relay(reader, writer)
            writer.write.assert_not_called()
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_closes_writer_on_read_exception(self):
        async def _test():
            reader = MagicMock()
            reader.read = AsyncMock(side_effect=ConnectionResetError("peer reset"))
            writer = MagicMock()
            writer.close = MagicMock()
            await relay(reader, writer)
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_still_closes_writer_if_close_raises(self):
        """relay must not propagate exceptions raised by writer.close()."""
        async def _test():
            reader = asyncio.StreamReader()
            reader.feed_eof()
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock(side_effect=OSError("already closed"))
            # Should complete without raising
            await relay(reader, writer)

        asyncio.run(_test())


# ─────────────────────────────────────────────
# handle_tls
# ─────────────────────────────────────────────


class TestHandleTls:
    @staticmethod
    def _make_client(data: bytes):
        """Return a (reader, writer) pair pre-loaded with data. Must be called inside an event loop."""
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("127.0.0.1", 12345))
        writer.close = MagicMock()
        writer.drain = AsyncMock()
        writer.write = MagicMock()
        return reader, writer

    @staticmethod
    def _upstream_pair():
        upstream_r = asyncio.StreamReader()
        upstream_r.feed_eof()
        upstream_w = MagicMock()
        upstream_w.drain = AsyncMock()
        upstream_w.close = MagicMock()
        upstream_w.write = MagicMock()
        return upstream_r, upstream_w

    def test_closes_when_no_sni(self):
        async def _test():
            reader, writer = self._make_client(b"\x00" * 100)
            await handle_tls(reader, writer)
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_closes_for_static_host(self):
        async def _test():
            reader, writer = self._make_client(build_client_hello("mystatic.local"))
            original = proxy_module.STATIC_DOMAIN
            try:
                proxy_module.STATIC_DOMAIN = "mystatic.local"
                await handle_tls(reader, writer)
            finally:
                proxy_module.STATIC_DOMAIN = original
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_closes_for_disallowed_host(self):
        async def _test():
            reader, writer = self._make_client(build_client_hello("evil.com"))
            original = proxy_module.ALLOWED_HOSTS
            try:
                proxy_module.ALLOWED_HOSTS = [_wildcard_to_regex("good.com")]
                await handle_tls(reader, writer)
            finally:
                proxy_module.ALLOWED_HOSTS = original
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_allows_any_host_when_allowed_hosts_is_none(self):
        async def _test():
            reader, writer = self._make_client(build_client_hello("anything.com"))
            mock_open = AsyncMock(side_effect=ConnectionRefusedError("no upstream in test"))
            original_allowed, original_static = proxy_module.ALLOWED_HOSTS, proxy_module.STATIC_DOMAIN
            try:
                proxy_module.ALLOWED_HOSTS = None
                proxy_module.STATIC_DOMAIN = ""
                with patch("asyncio.open_connection", mock_open):
                    await handle_tls(reader, writer)
            finally:
                proxy_module.ALLOWED_HOSTS = original_allowed
                proxy_module.STATIC_DOMAIN = original_static
            # Connection was attempted (not blocked by host filtering)
            mock_open.assert_called_once_with("anything.com", proxy_module.UPSTREAM_PORT)

        asyncio.run(_test())

    def test_proxies_host_in_allowed_list(self):
        async def _test():
            reader, writer = self._make_client(build_client_hello("good.com"))
            mock_open = AsyncMock(side_effect=ConnectionRefusedError("no upstream in test"))
            original_allowed, original_static = proxy_module.ALLOWED_HOSTS, proxy_module.STATIC_DOMAIN
            try:
                proxy_module.ALLOWED_HOSTS = [_wildcard_to_regex("good.com")]
                proxy_module.STATIC_DOMAIN = ""
                with patch("asyncio.open_connection", mock_open):
                    await handle_tls(reader, writer)
            finally:
                proxy_module.ALLOWED_HOSTS = original_allowed
                proxy_module.STATIC_DOMAIN = original_static
            mock_open.assert_called_once_with("good.com", proxy_module.UPSTREAM_PORT)

        asyncio.run(_test())

    def test_closes_when_upstream_connection_fails(self):
        async def _test():
            reader, writer = self._make_client(build_client_hello("unreachable.com"))
            original_allowed, original_static = proxy_module.ALLOWED_HOSTS, proxy_module.STATIC_DOMAIN
            try:
                proxy_module.ALLOWED_HOSTS = None
                proxy_module.STATIC_DOMAIN = ""
                with patch(
                    "asyncio.open_connection",
                    side_effect=ConnectionRefusedError("refused"),
                ):
                    await handle_tls(reader, writer)
            finally:
                proxy_module.ALLOWED_HOSTS = original_allowed
                proxy_module.STATIC_DOMAIN = original_static
            writer.close.assert_called_once()

        asyncio.run(_test())


# ─────────────────────────────────────────────
# Integration: TLS proxy with real servers
# ─────────────────────────────────────────────


class TestTlsProxy:

    def test_proxy_relays_client_hello(self, proxy_server):
        """Bytes sent through the proxy are forwarded to upstream and echoed back."""
        hello = build_client_hello("localhost")
        received = _connect_and_relay(proxy_server.proxy_port, hello)
        assert received == hello

    def test_proxy_closes_on_no_sni(self, proxy_server):
        """Non-TLS data with no SNI causes the proxy to close the connection."""
        received = _connect_and_relay(proxy_server.proxy_port, b"not tls data at all")
        assert received == b""

    def test_proxy_closes_on_static_host(self, proxy_server, monkeypatch):
        """SNI hostname matching STATIC_DOMAIN causes the proxy to close the connection."""
        monkeypatch.setattr(proxy_module, "STATIC_DOMAIN", "localhost")
        hello = build_client_hello("localhost")
        received = _connect_and_relay(proxy_server.proxy_port, hello)
        assert received == b""

    def test_proxy_closes_on_disallowed_host(self, proxy_server, monkeypatch):
        """SNI hostname not in ALLOWED_HOSTS causes the proxy to close the connection."""
        monkeypatch.setattr(proxy_module, "ALLOWED_HOSTS", [_wildcard_to_regex("other.com")])
        hello = build_client_hello("localhost")
        received = _connect_and_relay(proxy_server.proxy_port, hello)
        assert received == b""


# ─────────────────────────────────────────────
# _parse_allowed_hosts
# ─────────────────────────────────────────────


class TestParseAllowedHosts:
    def test_returns_none_when_env_not_set(self):
        assert _parse_allowed_hosts(None) is None

    def test_returns_none_for_empty_string(self):
        assert _parse_allowed_hosts("") is None

    def test_single_pattern(self):
        result = _parse_allowed_hosts("example.com")
        assert result is not None
        assert len(result) == 1

    def test_multiple_patterns(self):
        result = _parse_allowed_hosts("foo.com,bar.com")
        assert result is not None
        assert len(result) == 2

    def test_strips_whitespace(self):
        result = _parse_allowed_hosts(" foo.com , bar.com ")
        assert result is not None
        assert len(result) == 2

    def test_exact_pattern_matches_hostname(self):
        result = _parse_allowed_hosts("example.com")
        assert result is not None
        assert result[0].fullmatch("example.com")

    def test_exact_pattern_does_not_match_subdomain(self):
        result = _parse_allowed_hosts("example.com")
        assert result is not None
        assert not result[0].fullmatch("sub.example.com")

    def test_leading_wildcard_matches_single_level_subdomain(self):
        result = _parse_allowed_hosts("*.example.com")
        assert result is not None
        assert result[0].fullmatch("something.example.com")

    def test_leading_wildcard_matches_multi_level_subdomain(self):
        result = _parse_allowed_hosts("*.example.com")
        assert result is not None
        assert result[0].fullmatch("something.anotherthing.example.com")

    def test_leading_wildcard_does_not_match_bare_domain(self):
        result = _parse_allowed_hosts("*.example.com")
        assert result is not None
        assert not result[0].fullmatch("example.com")

    def test_inline_wildcard_matches_single_label(self):
        result = _parse_allowed_hosts("some*.blah.com")
        assert result is not None
        assert result[0].fullmatch("something.blah.com")

    def test_inline_wildcard_does_not_match_across_labels(self):
        result = _parse_allowed_hosts("some*.blah.com")
        assert result is not None
        assert not result[0].fullmatch("something.anotherthing.blah.com")

    def test_multiple_patterns_match_as_or(self):
        result = _parse_allowed_hosts("good.com,*.internal.net")
        assert result is not None
        assert any(p.fullmatch("good.com") for p in result)
        assert any(p.fullmatch("api.internal.net") for p in result)
        assert not any(p.fullmatch("evil.com") for p in result)

    def test_empty_list_blocks_all_hosts(self):
        async def _test():
            reader = asyncio.StreamReader()
            reader.feed_data(build_client_hello("anything.com"))
            reader.feed_eof()
            writer = MagicMock()
            writer.get_extra_info = MagicMock(return_value=("127.0.0.1", 12345))
            writer.close = MagicMock()
            writer.drain = AsyncMock()
            writer.write = MagicMock()
            original_allowed, original_static = proxy_module.ALLOWED_HOSTS, proxy_module.STATIC_DOMAIN
            try:
                proxy_module.ALLOWED_HOSTS = []
                proxy_module.STATIC_DOMAIN = ""
                await handle_tls(reader, writer)
            finally:
                proxy_module.ALLOWED_HOSTS = original_allowed
                proxy_module.STATIC_DOMAIN = original_static
            writer.close.assert_called_once()

        asyncio.run(_test())


# ─────────────────────────────────────────────
# ALLOWED_HOSTS env var loading
# ─────────────────────────────────────────────


class TestAllowedHostsEnvVar:
    def test_module_loads_allowed_hosts_from_env(self, monkeypatch):
        import importlib
        monkeypatch.setenv("ALLOWED_HOSTS", "example.com,*.internal")
        import wonderwall.https_proxy as m
        importlib.reload(m)
        assert m.ALLOWED_HOSTS is not None
        assert len(m.ALLOWED_HOSTS) == 2
        assert m.ALLOWED_HOSTS[0].fullmatch("example.com")
        assert m.ALLOWED_HOSTS[1].fullmatch("api.internal")

    def test_module_allows_all_when_env_not_set(self, monkeypatch):
        import importlib
        monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
        import wonderwall.https_proxy as m
        importlib.reload(m)
        assert m.ALLOWED_HOSTS is None


# ─────────────────────────────────────────────
# STATIC_DOMAIN env var loading
# ─────────────────────────────────────────────


class TestStaticDomainEnvVar:
    def test_module_loads_static_domain_from_env(self, monkeypatch):
        import importlib
        monkeypatch.setenv("STATIC_DOMAIN", "mystatic.local")
        import wonderwall.https_proxy as m
        importlib.reload(m)
        assert m.STATIC_DOMAIN == "mystatic.local"

    def test_module_defaults_static_domain_to_hostname(self, monkeypatch):
        import importlib
        import socket
        monkeypatch.delenv("STATIC_DOMAIN", raising=False)
        import wonderwall.https_proxy as m
        importlib.reload(m)
        assert m.STATIC_DOMAIN == socket.gethostname()
