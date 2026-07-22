"""Tests for wonderwall TCP port forwarding (wonderwall/port_forward.py)."""

import asyncio
import socket
import threading
import time
from collections import namedtuple

import pytest

import wonderwall.port_forward as port_forward_module
from wonderwall.port_forward import _parse_port_forwards, start_port_forwarders


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


def _connect_send_and_recv(port: int, data: bytes, timeout: float = 3.0) -> bytes:
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
# _parse_port_forwards
# ─────────────────────────────────────────────


class TestParsePortForwards:
    def test_returns_empty_list_when_env_not_set(self):
        assert _parse_port_forwards(None) == []

    def test_returns_empty_list_for_empty_string(self):
        assert _parse_port_forwards("") == []

    def test_single_port_forwards_to_itself(self):
        assert _parse_port_forwards("5432") == [(5432, 5432)]

    def test_remapped_port(self):
        assert _parse_port_forwards("15432:5432") == [(15432, 5432)]

    def test_multiple_entries(self):
        assert _parse_port_forwards("6379,15432:5432") == [(6379, 6379), (15432, 5432)]

    def test_strips_whitespace(self):
        assert _parse_port_forwards(" 6379 , 15432 : 5432 ") == [(6379, 6379), (15432, 5432)]

    def test_skips_non_integer_port(self, caplog):
        with caplog.at_level("WARNING"):
            assert _parse_port_forwards("abc") == []
        assert "malformed" in caplog.text.lower()

    def test_skips_entry_with_too_many_colons(self, caplog):
        with caplog.at_level("WARNING"):
            assert _parse_port_forwards("1:2:3") == []
        assert "malformed" in caplog.text.lower()

    def test_skips_port_zero(self, caplog):
        with caplog.at_level("WARNING"):
            assert _parse_port_forwards("0") == []
        assert "1-65535" in caplog.text

    def test_skips_port_out_of_range(self, caplog):
        with caplog.at_level("WARNING"):
            assert _parse_port_forwards("70000") == []
        assert "1-65535" in caplog.text

    def test_skips_non_integer_target_port(self, caplog):
        with caplog.at_level("WARNING"):
            assert _parse_port_forwards("1:abc") == []
        assert "malformed" in caplog.text.lower()

    def test_valid_entries_kept_alongside_invalid_ones(self, caplog):
        with caplog.at_level("WARNING"):
            result = _parse_port_forwards("5432,abc,6379")
        assert result == [(5432, 5432), (6379, 6379)]

    def test_skips_duplicate_listen_port(self, caplog):
        with caplog.at_level("WARNING"):
            result = _parse_port_forwards("5432,5432:6543")
        assert result == [(5432, 5432)]
        assert "duplicate" in caplog.text.lower()


# ─────────────────────────────────────────────
# Integration: real forwarder relaying to a real target
# ─────────────────────────────────────────────


@pytest.fixture()
def forward_target_server():
    """Starts a real echo asyncio server on a random port, standing in for a host service."""
    port = _free_port()

    async def echo_handler(reader, writer):
        try:
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except OSError:
                pass

    loop = asyncio.new_event_loop()
    servers = {}

    async def start():
        servers["target"] = await asyncio.start_server(echo_handler, "127.0.0.1", port)

    loop.run_until_complete(start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    _wait_for_port(port)

    yield port

    async def stop():
        servers["target"].close()
        await servers["target"].wait_closed()

    asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5.0)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)
    loop.close()


@pytest.fixture()
def running_forwarder(monkeypatch):
    """Starts real port-forward servers per FORWARD_PORTS/FORWARD_TARGET_HOST set by the test."""
    loop = asyncio.new_event_loop()
    state = {}

    def _start(forward_ports, target_host="127.0.0.1"):
        monkeypatch.setattr(port_forward_module, "FORWARD_PORTS", forward_ports)
        monkeypatch.setattr(port_forward_module, "FORWARD_TARGET_HOST", target_host)

        async def start():
            state["servers"] = await start_port_forwarders()

        loop.run_until_complete(start())
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        state["thread"] = thread
        for listen_port, _ in forward_ports:
            _wait_for_port(listen_port)
        return ForwarderInfo(listen_ports=[lp for lp, _ in forward_ports])

    ForwarderInfo = namedtuple("ForwarderInfo", ["listen_ports"])
    yield _start

    async def stop():
        for s in state.get("servers", []):
            s.close()
            await s.wait_closed()

    if "servers" in state:
        asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5.0)
        loop.call_soon_threadsafe(loop.stop)
        state["thread"].join(timeout=2.0)
    loop.close()


class TestPortForwardRelay:
    def test_relays_bytes_to_target_and_back(self, forward_target_server, running_forwarder):
        listen_port = _free_port()
        running_forwarder([(listen_port, forward_target_server)])

        received = _connect_send_and_recv(listen_port, b"hello wonderwall")
        assert received == b"hello wonderwall"

    def test_supports_port_remapping(self, forward_target_server, running_forwarder):
        listen_port = _free_port()
        running_forwarder([(listen_port, forward_target_server)])

        received = _connect_send_and_recv(listen_port, b"remapped")
        assert received == b"remapped"

    def test_closes_client_cleanly_when_target_unreachable(self, running_forwarder):
        unreachable_port = _free_port()  # nothing listening here
        listen_port = _free_port()
        running_forwarder([(listen_port, unreachable_port)])

        received = _connect_send_and_recv(listen_port, b"anything")
        assert received == b""
