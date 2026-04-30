import asyncio
import re
import socket
import threading
import time
from collections import namedtuple
from functools import partial
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler

import pytest

import wonderwall.https_proxy as proxy_module
import wonderwall.http_proxy as static_module
from wonderwall.http_proxy import HttpProxyHandler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Server on port {port} did not start within {timeout}s")


@pytest.fixture()
def static_server(tmp_path, monkeypatch):
    """Starts a real HTTPServer on a random port serving from a temp directory."""
    port = _free_port()
    monkeypatch.setenv("HTTP_PORT", str(port))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(static_module, "STATIC_DOMAIN", "127.0.0.1")

    handler = partial(HttpProxyHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for_port(port)

    ServerInfo = namedtuple("ServerInfo", ["base_url", "root"])
    yield ServerInfo(base_url=f"http://127.0.0.1:{port}", root=tmp_path)

    server.shutdown()
    thread.join(timeout=2.0)


@pytest.fixture()
def upstream_server(tmp_path):
    """Starts a second real HTTPServer on a random port for use as an HTTP upstream."""
    port = _free_port()
    root = tmp_path / "upstream"
    root.mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for_port(port)

    UpstreamInfo = namedtuple("UpstreamInfo", ["base_url", "root", "port"])
    yield UpstreamInfo(base_url=f"http://127.0.0.1:{port}", root=root, port=port)

    server.shutdown()
    thread.join(timeout=2.0)


@pytest.fixture()
def method_upstream_server(tmp_path):
    """HTTPServer that responds 200 to any HTTP method, echoing the method in the body."""
    from http.server import BaseHTTPRequestHandler

    class AnyMethodHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress output

        def handle_request(self):
            body = f"{self.command} OK".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = handle_request

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), AnyMethodHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for_port(port)

    UpstreamInfo = namedtuple("UpstreamInfo", ["base_url", "root", "port"])
    yield UpstreamInfo(base_url=f"http://127.0.0.1:{port}", root=tmp_path, port=port)

    server.shutdown()
    thread.join(timeout=2.0)


@pytest.fixture()
def proxy_server(monkeypatch):
    """Starts a real SNI proxy backed by an echo upstream, both on random ports."""
    upstream_port = _free_port()
    proxy_port = _free_port()

    monkeypatch.setattr(proxy_module, "UPSTREAM_PORT", upstream_port)
    monkeypatch.setattr(proxy_module, "ALLOWED_HOSTS", [re.compile(r"localhost")])

    loop = asyncio.new_event_loop()
    servers = {}

    async def echo_handler(reader, writer):
        try:
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def start():
        servers["upstream"] = await asyncio.start_server(
            echo_handler, "", upstream_port  # all interfaces: works for both 127.0.0.1 and ::1
        )
        servers["proxy"] = await asyncio.start_server(
            proxy_module.handle_tls, "127.0.0.1", proxy_port
        )

    loop.run_until_complete(start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    _wait_for_port(proxy_port)

    ProxyInfo = namedtuple("ProxyInfo", ["proxy_port", "upstream_port"])
    yield ProxyInfo(proxy_port=proxy_port, upstream_port=upstream_port)

    async def stop():
        for s in servers.values():
            s.close()
            await s.wait_closed()

    asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5.0)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)
    loop.close()
