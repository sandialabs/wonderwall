"""Tests for wonderwall entry point (wonderwall/__main__.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wonderwall.__main__ import main

# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────


class TestMain:
    def test_raises_when_dns_ip_missing(self, monkeypatch):
        monkeypatch.delenv("DNS_A_RECORD_IP", raising=False)
        # Patch module-level DNS_A_RECORD_IP to None
        with patch("wonderwall.__main__.DNS_A_RECORD_IP", None):
            with pytest.raises(ValueError, match="DNS_A_RECORD_IP"):
                asyncio.run(main())

    def test_starts_all_services(self, monkeypatch):
        mock_server = MagicMock()
        mock_server.__aenter__ = AsyncMock(return_value=mock_server)
        mock_server.__aexit__ = AsyncMock(return_value=None)
        mock_server.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("wonderwall.__main__.DNS_A_RECORD_IP", "1.2.3.4"),
            patch(
                "asyncio.start_server", AsyncMock(return_value=mock_server)
            ) as mock_start,
            patch("threading.Thread") as mock_thread,
            patch("wonderwall.logging_configuration.setup_logger"),
        ):
            with pytest.raises((asyncio.CancelledError, RuntimeError)):
                asyncio.run(main())

        mock_start.assert_called_once()
        assert mock_thread.call_count == 2

    def test_raises_on_forward_port_collision_with_reserved_port(self, monkeypatch):
        with (
            patch("wonderwall.__main__.DNS_A_RECORD_IP", "1.2.3.4"),
            patch("wonderwall.__main__.FORWARD_PORTS", [(443, 443)]),
        ):
            with pytest.raises(ValueError, match="FORWARD_PORTS listen port 443"):
                asyncio.run(main())

    def test_starts_configured_port_forwarders_alongside_sni_proxy(self, monkeypatch):
        mock_server = MagicMock()
        mock_server.__aenter__ = AsyncMock(return_value=mock_server)
        mock_server.__aexit__ = AsyncMock(return_value=None)
        mock_server.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)

        mock_forward_server = MagicMock()
        mock_forward_server.__aenter__ = AsyncMock(return_value=mock_forward_server)
        mock_forward_server.__aexit__ = AsyncMock(return_value=None)
        mock_forward_server.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("wonderwall.__main__.DNS_A_RECORD_IP", "1.2.3.4"),
            patch("wonderwall.__main__.FORWARD_PORTS", [(15432, 5432)]),
            patch(
                "wonderwall.__main__.start_port_forwarders",
                AsyncMock(return_value=[mock_forward_server]),
            ) as mock_start_forwarders,
            patch(
                "asyncio.start_server", AsyncMock(return_value=mock_server)
            ) as mock_start,
            patch("threading.Thread") as mock_thread,
            patch("wonderwall.logging_configuration.setup_logger"),
        ):
            with pytest.raises((asyncio.CancelledError, RuntimeError)):
                asyncio.run(main())

        mock_start.assert_called_once()
        mock_start_forwarders.assert_called_once()
        assert mock_thread.call_count == 2
        mock_server.serve_forever.assert_called_once()
        mock_forward_server.serve_forever.assert_called_once()
