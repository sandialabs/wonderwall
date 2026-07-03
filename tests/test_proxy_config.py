"""Tests for wonderwall forward-proxy env var helpers (wonderwall/proxy_config.py)."""

import importlib

import wonderwall.proxy_config as proxy_config
from wonderwall.proxy_config import _get_proxy_url, _hostname_bypasses_proxy, _parse_no_proxy

_ALL_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
_ALL_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


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
