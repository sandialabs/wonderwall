"""Tests for wonderwall forward-proxy env var helpers (wonderwall/proxy_config.py)."""

import asyncio
import importlib
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import wonderwall.proxy_config as proxy_config
from wonderwall.proxy_config import (
    _connect_via_proxy,
    _env_flag,
    _get_proxy_url,
    _hostname_bypasses_proxy,
    _parse_no_proxy,
    _parse_proxy_host_port,
    open_upstream_connection,
)

_ALL_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
_ALL_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


@pytest.fixture(autouse=True)
def _reset_auto_proxy_cache():
    """Ensure AUTO_PROXY's DNS-unresolvable cache never leaks between tests, regardless of order."""
    proxy_config._proxy_unresolvable_until = 0.0
    yield
    proxy_config._proxy_unresolvable_until = 0.0


# ─────────────────────────────────────────────
# _get_proxy_url
# ─────────────────────────────────────────────


class TestGetProxyUrl:
    def test_returns_none_for_empty_env(self):
        assert _get_proxy_url({}) is None

    def test_returns_https_proxy_uppercase(self):
        assert _get_proxy_url({"HTTPS_PROXY": "http://proxy:3128"}) == "http://proxy:3128"

    def test_uppercase_https_beats_lowercase_https(self):
        env = {"HTTPS_PROXY": "http://upper:3128", "https_proxy": "http://lower:3128"}
        assert _get_proxy_url(env) == "http://upper:3128"

    def test_uppercase_https_beats_uppercase_http(self):
        env = {"HTTPS_PROXY": "http://https-proxy:3128", "HTTP_PROXY": "http://http-proxy:3128"}
        assert _get_proxy_url(env) == "http://https-proxy:3128"

    def test_falls_through_to_lowercase_http_proxy_as_last_resort(self):
        assert _get_proxy_url({"http_proxy": "http://last-resort:3128"}) == "http://last-resort:3128"

    def test_skips_empty_string_values(self):
        env = {"HTTPS_PROXY": "", "https_proxy": "http://lower:3128"}
        assert _get_proxy_url(env) == "http://lower:3128"

    def test_defaults_to_os_environ(self, monkeypatch):
        for var in _ALL_PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://from-environ:3128")
        assert _get_proxy_url() == "http://from-environ:3128"


# ─────────────────────────────────────────────
# _env_flag
# ─────────────────────────────────────────────


class TestEnvFlag:
    def test_returns_false_for_empty_env(self):
        assert _env_flag("AUTO_PROXY", {}) is False

    def test_recognizes_truthy_values_case_insensitively(self):
        for value in ("1", "true", "True", "TRUE", "yes", "Yes", "on", "ON"):
            assert _env_flag("AUTO_PROXY", {"AUTO_PROXY": value}) is True

    def test_returns_false_for_other_values(self):
        for value in ("0", "false", "no", "off", "nope", ""):
            assert _env_flag("AUTO_PROXY", {"AUTO_PROXY": value}) is False

    def test_strips_whitespace(self):
        assert _env_flag("AUTO_PROXY", {"AUTO_PROXY": "  true  "}) is True

    def test_defaults_to_os_environ(self, monkeypatch):
        monkeypatch.setenv("AUTO_PROXY", "true")
        assert _env_flag("AUTO_PROXY") is True


# ─────────────────────────────────────────────
# _parse_no_proxy
# ─────────────────────────────────────────────


class TestParseNoProxy:
    def test_returns_empty_list_for_none(self):
        assert _parse_no_proxy(None) == []

    def test_returns_empty_list_for_empty_string(self):
        assert _parse_no_proxy("") == []

    def test_single_entry(self):
        assert _parse_no_proxy("example.com") == ["example.com"]

    def test_multiple_entries(self):
        assert _parse_no_proxy("foo.com,bar.com") == ["foo.com", "bar.com"]

    def test_strips_whitespace(self):
        assert _parse_no_proxy(" foo.com , bar.com ") == ["foo.com", "bar.com"]

    def test_lowercases_entries(self):
        assert _parse_no_proxy("Example.COM") == ["example.com"]

    def test_strips_leading_dot(self):
        assert _parse_no_proxy(".example.com") == ["example.com"]

    def test_preserves_bare_asterisk(self):
        assert _parse_no_proxy("*") == ["*"]

    def test_skips_empty_entries_from_stray_commas(self):
        assert _parse_no_proxy("foo.com,,bar.com,") == ["foo.com", "bar.com"]


# ─────────────────────────────────────────────
# _hostname_bypasses_proxy
# ─────────────────────────────────────────────


class TestHostnameBypassesProxy:
    def test_empty_list_returns_false(self):
        assert _hostname_bypasses_proxy("example.com", []) is False

    def test_exact_match(self):
        assert _hostname_bypasses_proxy("example.com", ["example.com"]) is True

    def test_subdomain_suffix_match(self):
        assert _hostname_bypasses_proxy("www.example.com", ["example.com"]) is True

    def test_unrelated_host_returns_false(self):
        assert _hostname_bypasses_proxy("other.com", ["example.com"]) is False

    def test_suffix_without_dot_does_not_match(self):
        assert _hostname_bypasses_proxy("notexample.com", ["example.com"]) is False

    def test_case_insensitive(self):
        assert _hostname_bypasses_proxy("WWW.EXAMPLE.COM", ["example.com"]) is True

    def test_bare_asterisk_matches_everything(self):
        assert _hostname_bypasses_proxy("anything.at.all", ["*"]) is True

    def test_matches_across_multi_entry_list(self):
        no_proxy = ["foo.com", "bar.com"]
        assert _hostname_bypasses_proxy("bar.com", no_proxy) is True
        assert _hostname_bypasses_proxy("baz.com", no_proxy) is False


# ─────────────────────────────────────────────
# _parse_proxy_host_port
# ─────────────────────────────────────────────


class TestParseProxyHostPort:
    def test_parses_host_and_port(self):
        assert _parse_proxy_host_port("http://proxy:3128") == ("proxy", 3128)

    def test_defaults_port_when_unspecified(self):
        assert _parse_proxy_host_port("http://proxy") == ("proxy", 80)

    def test_ignores_scheme(self):
        assert _parse_proxy_host_port("https://proxy:3128") == ("proxy", 3128)

    def test_raises_connection_error_when_no_host(self):
        try:
            _parse_proxy_host_port("proxy:3128")
            assert False, "expected ConnectionError"
        except ConnectionError:
            pass


# ─────────────────────────────────────────────
# _connect_via_proxy
# ─────────────────────────────────────────────


def _fake_proxy_pair(response: bytes | None = None):
    """Return a (reader, writer) pair simulating a proxy connection."""
    reader = asyncio.StreamReader()
    if response is not None:
        reader.feed_data(response)
    reader.feed_eof()
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    return reader, writer


class TestConnectViaProxy:
    def test_successful_connect_returns_reader_and_writer(self):
        async def _test():
            reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 Connection established\r\n\r\n")
            with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
                result_r, result_w = await _connect_via_proxy("proxy", 3128, "example.com", 443)
            assert result_r is reader
            assert result_w is writer

        asyncio.run(_test())

    def test_writes_correct_connect_request_bytes(self):
        async def _test():
            reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 Connection established\r\n\r\n")
            with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
                await _connect_via_proxy("proxy", 3128, "example.com", 443)
            writer.write.assert_called_once_with(
                b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
            )

        asyncio.run(_test())

    def test_connects_to_proxy_host_and_port_not_target(self):
        async def _test():
            reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 Connection established\r\n\r\n")
            mock_open = AsyncMock(return_value=(reader, writer))
            with patch("asyncio.open_connection", mock_open):
                await _connect_via_proxy("proxy", 3128, "example.com", 443)
            mock_open.assert_called_once_with("proxy", 3128)

        asyncio.run(_test())

    def test_non_200_response_raises_and_closes_writer(self):
        async def _test():
            reader, writer = _fake_proxy_pair(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
                try:
                    await _connect_via_proxy("proxy", 3128, "example.com", 443)
                    assert False, "expected ConnectionError"
                except ConnectionError:
                    pass
            writer.close.assert_called_once()

        asyncio.run(_test())

    def test_malformed_truncated_response_raises_connection_error(self):
        async def _test():
            reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 ")  # no terminating \r\n\r\n, then EOF
            with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
                try:
                    await _connect_via_proxy("proxy", 3128, "example.com", 443)
                    assert False, "expected ConnectionError"
                except ConnectionError:
                    pass

        asyncio.run(_test())


# ─────────────────────────────────────────────
# open_upstream_connection
# ─────────────────────────────────────────────


class TestOpenUpstreamConnection:
    def test_connects_directly_when_no_proxy_configured(self):
        async def _test():
            original_proxy_url = proxy_config.PROXY_URL
            try:
                proxy_config.PROXY_URL = None
                mock_open = AsyncMock(side_effect=ConnectionRefusedError("no server in test"))
                with patch("asyncio.open_connection", mock_open):
                    try:
                        await open_upstream_connection("example.com", 443)
                    except ConnectionRefusedError:
                        pass
                mock_open.assert_called_once_with("example.com", 443)
            finally:
                proxy_config.PROXY_URL = original_proxy_url

        asyncio.run(_test())

    def test_connects_directly_when_hostname_bypassed(self):
        async def _test():
            original_proxy_url, original_no_proxy = proxy_config.PROXY_URL, proxy_config.NO_PROXY
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = ["example.com"]
                mock_open = AsyncMock(side_effect=ConnectionRefusedError("no server in test"))
                with patch("asyncio.open_connection", mock_open):
                    try:
                        await open_upstream_connection("example.com", 443)
                    except ConnectionRefusedError:
                        pass
                mock_open.assert_called_once_with("example.com", 443)
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy

        asyncio.run(_test())

    def test_routes_through_proxy_when_configured_and_not_bypassed(self):
        async def _test():
            original_proxy_url, original_no_proxy = proxy_config.PROXY_URL, proxy_config.NO_PROXY
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 Connection established\r\n\r\n")
                mock_open = AsyncMock(return_value=(reader, writer))
                with patch("asyncio.open_connection", mock_open):
                    await open_upstream_connection("example.com", 443)
                mock_open.assert_called_once_with("proxy", 3128)
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy

        asyncio.run(_test())

    def test_propagates_connect_error_when_proxy_refuses(self):
        async def _test():
            original_proxy_url, original_no_proxy = proxy_config.PROXY_URL, proxy_config.NO_PROXY
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                reader, writer = _fake_proxy_pair(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
                    try:
                        await open_upstream_connection("example.com", 443)
                        assert False, "expected ConnectionError"
                    except ConnectionError:
                        pass
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy

        asyncio.run(_test())

    def test_falls_back_to_direct_when_auto_proxy_enabled_and_proxy_unresolvable(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = True

                async def fake_open_connection(host, port):
                    if host == "proxy":
                        raise socket.gaierror("Name or service not known")
                    return _fake_proxy_pair()

                mock_open = AsyncMock(side_effect=fake_open_connection)
                with patch("asyncio.open_connection", mock_open):
                    await open_upstream_connection("example.com", 443)
                mock_open.assert_called_with("example.com", 443)
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())

    def test_does_not_fall_back_when_auto_proxy_disabled(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = False
                mock_open = AsyncMock(side_effect=socket.gaierror("Name or service not known"))
                with patch("asyncio.open_connection", mock_open):
                    try:
                        await open_upstream_connection("example.com", 443)
                        assert False, "expected socket.gaierror"
                    except socket.gaierror:
                        pass
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())

    def test_does_not_fall_back_for_non_dns_errors_when_auto_proxy_enabled(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = True
                mock_open = AsyncMock(side_effect=ConnectionRefusedError("no server in test"))
                with patch("asyncio.open_connection", mock_open):
                    try:
                        await open_upstream_connection("example.com", 443)
                        assert False, "expected ConnectionRefusedError"
                    except ConnectionRefusedError:
                        pass
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())

    def test_skips_proxy_when_recently_found_unresolvable(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = True
                proxy_config._proxy_unresolvable_until = time.monotonic() + 100

                mock_open = AsyncMock(return_value=_fake_proxy_pair())
                with patch("asyncio.open_connection", mock_open):
                    await open_upstream_connection("example.com", 443)
                mock_open.assert_called_once_with("example.com", 443)
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())

    def test_retries_proxy_after_recheck_window_elapses(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = True
                # _proxy_unresolvable_until is 0.0 via the autouse fixture, i.e. already expired.

                reader, writer = _fake_proxy_pair(b"HTTP/1.1 200 Connection established\r\n\r\n")
                mock_open = AsyncMock(return_value=(reader, writer))
                with patch("asyncio.open_connection", mock_open):
                    await open_upstream_connection("example.com", 443)
                mock_open.assert_called_once_with("proxy", 3128)
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())

    def test_gaierror_sets_recheck_window(self):
        async def _test():
            original_proxy_url, original_no_proxy, original_auto_proxy = (
                proxy_config.PROXY_URL,
                proxy_config.NO_PROXY,
                proxy_config.AUTO_PROXY,
            )
            try:
                proxy_config.PROXY_URL = "http://proxy:3128"
                proxy_config.NO_PROXY = []
                proxy_config.AUTO_PROXY = True

                async def fake_open_connection(host, port):
                    if host == "proxy":
                        raise socket.gaierror("Name or service not known")
                    return _fake_proxy_pair()

                mock_open = AsyncMock(side_effect=fake_open_connection)
                before = time.monotonic()
                with patch("asyncio.open_connection", mock_open):
                    await open_upstream_connection("example.com", 443)
                expected = before + proxy_config.AUTO_PROXY_RECHECK_SECONDS
                assert expected - 1 <= proxy_config._proxy_unresolvable_until <= expected + 1
            finally:
                proxy_config.PROXY_URL = original_proxy_url
                proxy_config.NO_PROXY = original_no_proxy
                proxy_config.AUTO_PROXY = original_auto_proxy

        asyncio.run(_test())


# ─────────────────────────────────────────────
# PROXY_URL env var loading
# ─────────────────────────────────────────────


class TestProxyUrlEnvVar:
    def test_module_loads_proxy_url_from_env(self, monkeypatch):
        for var in _ALL_PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
        importlib.reload(proxy_config)
        try:
            assert proxy_config.PROXY_URL == "http://proxy:3128"
        finally:
            for var in _ALL_PROXY_VARS:
                monkeypatch.delenv(var, raising=False)
            importlib.reload(proxy_config)

    def test_module_defaults_proxy_url_to_none_when_unset(self, monkeypatch):
        for var in _ALL_PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        importlib.reload(proxy_config)
        assert proxy_config.PROXY_URL is None


# ─────────────────────────────────────────────
# NO_PROXY env var loading
# ─────────────────────────────────────────────


class TestNoProxyEnvVar:
    def test_module_loads_no_proxy_from_env(self, monkeypatch):
        for var in _ALL_NO_PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NO_PROXY", "example.com,internal.net")
        importlib.reload(proxy_config)
        try:
            assert proxy_config.NO_PROXY == ["example.com", "internal.net"]
        finally:
            for var in _ALL_NO_PROXY_VARS:
                monkeypatch.delenv(var, raising=False)
            importlib.reload(proxy_config)

    def test_module_defaults_no_proxy_to_empty_list_when_unset(self, monkeypatch):
        for var in _ALL_NO_PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        importlib.reload(proxy_config)
        assert proxy_config.NO_PROXY == []


# ─────────────────────────────────────────────
# AUTO_PROXY env var loading
# ─────────────────────────────────────────────


class TestAutoProxyEnvVar:
    def test_module_loads_auto_proxy_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTO_PROXY", "true")
        importlib.reload(proxy_config)
        try:
            assert proxy_config.AUTO_PROXY is True
        finally:
            monkeypatch.delenv("AUTO_PROXY", raising=False)
            importlib.reload(proxy_config)

    def test_module_defaults_auto_proxy_to_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("AUTO_PROXY", raising=False)
        importlib.reload(proxy_config)
        assert proxy_config.AUTO_PROXY is False


# ─────────────────────────────────────────────
# AUTO_PROXY_RECHECK_SECONDS env var loading
# ─────────────────────────────────────────────


class TestAutoProxyRecheckSecondsEnvVar:
    def test_module_loads_auto_proxy_recheck_seconds_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTO_PROXY_RECHECK_SECONDS", "45")
        importlib.reload(proxy_config)
        try:
            assert proxy_config.AUTO_PROXY_RECHECK_SECONDS == 45
        finally:
            monkeypatch.delenv("AUTO_PROXY_RECHECK_SECONDS", raising=False)
            importlib.reload(proxy_config)

    def test_module_defaults_auto_proxy_recheck_seconds_to_30_when_unset(self, monkeypatch):
        monkeypatch.delenv("AUTO_PROXY_RECHECK_SECONDS", raising=False)
        importlib.reload(proxy_config)
        assert proxy_config.AUTO_PROXY_RECHECK_SECONDS == 30
