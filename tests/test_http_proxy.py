"""Tests for wonderwall static file server (wonderwall/static.py)."""

import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
import requests

import wonderwall.http_proxy as static_module
from wonderwall import proxy_config
from wonderwall.http_proxy import HttpProxyHandler


# ─────────────────────────────────────────────
# HttpProxyHandler
# ─────────────────────────────────────────────


class TestHttpProxyHandler:
    def test_log_message_delegates_to_log(self):
        handler = MagicMock(spec=HttpProxyHandler)
        handler.request_id = "reqid"
        handler.address_string = MagicMock(return_value="1.2.3.4")

        with patch.object(static_module, "log") as mock_log:
            HttpProxyHandler.log_message(handler, "%s %s", "GET", "/index.html")

        mock_log.info.assert_called_once_with("[%s] HTTP %s %s", 'reqid', "1.2.3.4", "GET /index.html")

    def test_proxy_request_delegates_to_proxy_config_open_upstream_http_connection(self):
        handler = MagicMock(spec=HttpProxyHandler)
        handler.request_id = "reqid"
        handler.client_address = ("1.2.3.4", 5678)
        handler.path = "/proxied.txt"
        handler.headers = {"Host": "example.com:8080"}
        handler.wfile = MagicMock()

        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = MagicMock(
            status=200,
            getheaders=MagicMock(return_value=[("Content-Length", "5")]),
            read=MagicMock(side_effect=[b"hello", b""]),
        )
        mock_open = MagicMock(return_value=(fake_conn, "http://example.com:8080/proxied.txt"))
        with patch.object(static_module, "proxy_config") as mock_proxy_config:
            mock_proxy_config.open_upstream_http_connection = mock_open
            HttpProxyHandler._proxy_request(handler, "GET")

        mock_open.assert_called_once_with("example.com", 8080, "/proxied.txt", timeout=30)
        fake_conn.request.assert_called_once_with(
            "GET", "http://example.com:8080/proxied.txt", body=None, headers={"Host": "example.com:8080"}
        )


# ─────────────────────────────────────────────
# Integration: static file server
# ─────────────────────────────────────────────


class TestStaticFileServing:

    def test_existing_file_returns_200(self, static_server):
        (static_server.root / "hello.txt").write_text("hello world")
        r = requests.get(f"{static_server.base_url}/hello.txt")
        assert r.status_code == 200
        assert r.text == "hello world"

    def test_missing_file_returns_404(self, static_server):
        r = requests.get(f"{static_server.base_url}/nonexistent.txt")
        assert r.status_code == 404

    def test_directory_listing_returns_200(self, static_server):
        (static_server.root / "index.html").write_text("<h1>Hi</h1>")
        # SimpleHTTPRequestHandler may redirect bare "/" — requests follows it
        r = requests.get(f"{static_server.base_url}/")
        assert r.status_code == 200

    def test_directory_listing_contains_filename(self, static_server):
        (static_server.root / "somefile.txt").write_text("data")
        r = requests.get(f"{static_server.base_url}/")
        assert "somefile.txt" in r.text

    def test_nested_file(self, static_server):
        subdir = static_server.root / "sub"
        subdir.mkdir()
        (subdir / "data.json").write_text('{"key": "value"}')
        r = requests.get(f"{static_server.base_url}/sub/data.json")
        assert r.status_code == 200
        assert r.json() == {"key": "value"}

    def test_missing_nested_path_returns_404(self, static_server):
        r = requests.get(f"{static_server.base_url}/sub/missing.txt")
        assert r.status_code == 404

    def test_head_request(self, static_server):
        (static_server.root / "page.html").write_text("<html></html>")
        r = requests.head(f"{static_server.base_url}/page.html")
        assert r.status_code == 200
        assert r.headers.get("Content-Length") is not None
        assert r.content == b""

    def test_large_file(self, static_server):
        data = b"x" * 1_048_576
        (static_server.root / "bigfile.bin").write_bytes(data)
        r = requests.get(f"{static_server.base_url}/bigfile.bin")
        assert r.status_code == 200
        assert len(r.content) == 1_048_576

    def test_empty_file_returns_200(self, static_server):
        (static_server.root / "empty.txt").write_bytes(b"")
        r = requests.get(f"{static_server.base_url}/empty.txt")
        assert r.status_code == 200
        assert r.content == b""

    def test_path_traversal_blocked(self, static_server):
        r = requests.get(f"{static_server.base_url}/../etc/passwd")
        assert r.status_code != 200

    def test_concurrent_requests(self, static_server):
        (static_server.root / "a.txt").write_text("aaa")
        (static_server.root / "b.txt").write_text("bbb")
        urls = [
            f"{static_server.base_url}/a.txt",
            f"{static_server.base_url}/b.txt",
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(requests.get, urls))
        assert all(r.status_code == 200 for r in responses)
        bodies = {r.text for r in responses}
        assert bodies == {"aaa", "bbb"}


# ─────────────────────────────────────────────
# Domain filtering
# ─────────────────────────────────────────────


class TestDomainFiltering:

    def test_no_domain_configured_proxies_all(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "")
        r = requests.get(
            f"{static_server.base_url}/hi.txt",
            headers={"Host": "unreachable.invalid"},
            allow_redirects=False,
        )
        assert r.status_code == 502

    def test_matching_host_serves_file(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        (static_server.root / "index.html").write_text("<h1>ok</h1>")
        r = requests.get(
            f"{static_server.base_url}/index.html",
            headers={"Host": "files.example.com"},
            allow_redirects=False,
        )
        assert r.status_code == 200

    def test_matching_host_with_port_serves_file(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        (static_server.root / "doc.txt").write_text("data")
        r = requests.get(
            f"{static_server.base_url}/doc.txt",
            headers={"Host": "files.example.com:9000"},
            allow_redirects=False,
        )
        assert r.status_code == 200


# ─────────────────────────────────────────────
# HTTP proxy behavior
# ─────────────────────────────────────────────


class TestProxyBehavior:

    def test_wrong_host_proxies_get(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        (upstream_server.root / "proxied.txt").write_text("from upstream")
        r = requests.get(
            f"{static_server.base_url}/proxied.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200
        assert r.text == "from upstream"

    def test_wrong_host_proxies_head(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        (upstream_server.root / "headtest.html").write_text("<html></html>")
        r = requests.head(
            f"{static_server.base_url}/headtest.html",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200
        assert r.content == b""

    def test_proxy_preserves_path(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        subdir = upstream_server.root / "deep" / "path"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("deep content")
        r = requests.get(
            f"{static_server.base_url}/deep/path/file.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200
        assert r.text == "deep content"

    def test_proxy_404_from_upstream_forwarded(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        r = requests.get(
            f"{static_server.base_url}/nonexistent.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 404

    def test_unreachable_upstream_returns_502(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        r = requests.get(
            f"{static_server.base_url}/anything.txt",
            headers={"Host": "127.0.0.1:1"},  # port 1 is never open
            allow_redirects=False,
        )
        assert r.status_code == 502

    def test_invalid_port_in_host_header_returns_400(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        r = requests.get(
            f"{static_server.base_url}/anything.txt",
            headers={"Host": "example.com:99999"},
            allow_redirects=False,
        )
        assert r.status_code == 400


# ─────────────────────────────────────────────
# HTTP verb proxying
# ─────────────────────────────────────────────


class TestHttpVerbProxying:

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def test_verb_is_proxied(self, static_server, method_upstream_server, monkeypatch, method):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        r = requests.request(
            method,
            f"{static_server.base_url}/anything",
            headers={"Host": f"127.0.0.1:{method_upstream_server.port}"},
        )
        assert r.status_code == 200
        assert r.text == f"{method} OK"


# ─────────────────────────────────────────────
# ALLOWED_HOSTS filtering
# ─────────────────────────────────────────────


class TestAllowedHosts:

    def test_allowed_domain_is_proxied(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        monkeypatch.setattr(static_module, "ALLOWED_HOSTS", [re.compile(r"127\.0\.0\.1")])
        (upstream_server.root / "hi.txt").write_text("allowed")
        r = requests.get(
            f"{static_server.base_url}/hi.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200

    def test_disallowed_domain_returns_403(self, static_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        monkeypatch.setattr(static_module, "ALLOWED_HOSTS", [re.compile(r"allowed\.com")])
        r = requests.get(
            f"{static_server.base_url}/anything",
            headers={"Host": "evil.com"},
            allow_redirects=False,
        )
        assert r.status_code == 403

    def test_no_allowed_hosts_configured_proxies_all(self, static_server, upstream_server, monkeypatch):
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        monkeypatch.setattr(static_module, "ALLOWED_HOSTS", None)
        (upstream_server.root / "open.txt").write_text("open")
        r = requests.get(
            f"{static_server.base_url}/open.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200

    def test_wildcard_allowed_domain(self, static_server, upstream_server, monkeypatch):
        from wonderwall.https_proxy import _wildcard_to_regex
        monkeypatch.setattr(static_module, "STATIC_DOMAIN", "files.example.com")
        monkeypatch.setattr(static_module, "ALLOWED_HOSTS", [_wildcard_to_regex("127.0.0.*")])
        (upstream_server.root / "wc.txt").write_text("wildcard")
        r = requests.get(
            f"{static_server.base_url}/wc.txt",
            headers={"Host": f"127.0.0.1:{upstream_server.port}"},
            allow_redirects=False,
        )
        assert r.status_code == 200


# ─────────────────────────────────────────────
# Integration: real Squid sidecar (CI only — see conftest.real_squid_proxy)
# ─────────────────────────────────────────────


class TestProxyThroughRealSquid:
    def test_proxies_real_http_traffic_through_squid_to_internet(self, real_squid_proxy, static_server, monkeypatch):
        monkeypatch.setattr(proxy_config, "PROXY_URL", real_squid_proxy)
        monkeypatch.setattr(proxy_config, "NO_PROXY", [])

        r = requests.get(
            f"{static_server.base_url}/",
            headers={"Host": "example.com"},
            allow_redirects=False,
        )
        assert r.status_code == 200

