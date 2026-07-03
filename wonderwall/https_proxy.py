"""SNI extraction and TLS pass-through proxy."""

import asyncio
import logging
import os
import re
import socket
import struct
import uuid

from wonderwall import proxy_config
from wonderwall.transfer_stats import TransferStats

log = logging.getLogger(__name__)


def _escape_with_single_wildcards(s: str) -> str:
    """Escape s for regex, replacing * with [^.]* (matches within one label only)."""
    return r"[^.]*".join(re.escape(part) for part in s.split("*"))


def _wildcard_to_regex(pattern: str) -> re.Pattern:
    """Convert a wildcard host pattern to a compiled regex for fullmatch.

    '*.foo.com' (leading *.) matches any number of subdomain levels.
    'pre*.foo.com' (* within a label) matches only within that single label (no dots).
    """
    if pattern.startswith("*."):
        regex = r".+\." + _escape_with_single_wildcards(pattern[2:])
    else:
        regex = _escape_with_single_wildcards(pattern)
    return re.compile(regex)


def _parse_allowed_hosts(env_val: str | None) -> list[re.Pattern] | None:
    """Parse ALLOWED_HOSTS env var into compiled wildcard patterns, or None to allow all."""
    if not env_val:
        return None
    return [_wildcard_to_regex(p.strip()) for p in env_val.split(",") if p.strip()]


_TLS_CONTENT_TYPE_HANDSHAKE = 0x16
_TLS_HANDSHAKE_TYPE_CLIENT_HELLO = 0x01
_TLS_EXTENSION_SNI = 0x0000
_TLS_SNI_NAME_TYPE_HOST = 0x00

STATIC_DOMAIN = os.getenv(
    "STATIC_DOMAIN", socket.gethostname()
)  # HTTP-only host, never TLS proxied
ALLOWED_HOSTS = _parse_allowed_hosts(
    os.getenv("ALLOWED_HOSTS")
)  # None = allow any SNI hostname
UPSTREAM_PORT = int(os.getenv("UPSTREAM_PORT", "443"))
PEEK_BYTES = 512


def extract_sni(data: bytes) -> str | None:
    """Parse the SNI hostname from a TLS ClientHello record, or return None if not found."""
    try:
        if len(data) < 5 or data[0] != _TLS_CONTENT_TYPE_HANDSHAKE:
            return None
        pos = 5
        if data[pos] != _TLS_HANDSHAKE_TYPE_CLIENT_HELLO:
            return None
        pos += 4 + 2 + 32  # type, length, version, random
        sid_len = data[pos]
        pos += 1 + sid_len  # session id
        cs_len = struct.unpack("!H", data[pos : pos + 2])[0]
        pos += 2 + cs_len  # cipher suites
        cm_len = data[pos]
        pos += 1 + cm_len  # compression methods
        if pos + 2 > len(data):
            return None
        ext_end = pos + 2 + struct.unpack("!H", data[pos : pos + 2])[0]
        pos += 2
        while pos + 4 <= ext_end:
            ext_type = struct.unpack("!H", data[pos : pos + 2])[0]
            pos += 2
            ext_len = struct.unpack("!H", data[pos : pos + 2])[0]
            pos += 2
            if ext_type == _TLS_EXTENSION_SNI:
                pos += 3  # list length + name type
                name_len = struct.unpack("!H", data[pos : pos + 2])[0]
                pos += 2
                return data[pos : pos + name_len].decode("ascii")
            pos += ext_len
    except (IndexError, struct.error) as e:
        log.warning("Failed to parse SNI from packet: %s", e)
    return None


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, transfer_stats: TransferStats = None):
    """Forward bytes from reader → writer until EOF."""
    try:
        while data := await reader.read(4096):
            writer.write(data)
            if transfer_stats:
                transfer_stats.bytes_downstream += len(data)
                transfer_stats.log_transfer_update()
            await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
        log.warning("Relay error: %s", e)
    finally:
        try:
            writer.close()
        except OSError as e:
            log.warning("Error closing relay writer: %s", e)
    if transfer_stats:
        transfer_stats.log_transfer_update(True)
    


async def handle_tls(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter):
    """Accept a TLS connection, extract the SNI hostname, and relay traffic to the upstream."""
    addr = client_w.get_extra_info("peername")
    request_id = str(uuid.uuid4())[:8]  # Short 8-character request ID for readability
    start_time = asyncio.get_event_loop().time()

    try:
        # Read the first chunk — StreamReader buffers it, so we can prepend it
        # back for the upstream connection using a feeding trick below.
        peeked = await client_r.read(PEEK_BYTES)
        hostname = extract_sni(peeked)

        if not hostname:
            log.warning("[%s] %s: no SNI, closing", request_id, addr)
            return

        if STATIC_DOMAIN and hostname == STATIC_DOMAIN:
            log.warning("[%s] %s: %s is HTTP-only, closing", request_id, addr, hostname)
            return

        if ALLOWED_HOSTS is not None and not any(
            p.fullmatch(hostname) for p in ALLOWED_HOSTS
        ):
            log.warning("[%s] %s: %s not in allowed hosts, closing", request_id, addr, hostname)
            return

        log.info("[%s] %s → %s", request_id, addr, hostname)
        upstream_r, upstream_w = await proxy_config.open_upstream_connection(hostname, UPSTREAM_PORT)

        # Create transfer stats tracker
        transfer_stats = TransferStats(request_id, addr, hostname)

        # Forward client data upstream. Use write_eof() instead of close() so
        # upstream_r stays open to receive the upstream's response.
        async def client_to_upstream():
            try:
                upstream_w.write(peeked)
                transfer_stats.bytes_upstream += len(peeked)
                await upstream_w.drain()
                transfer_stats.log_transfer_update()

                while data := await client_r.read(4096):
                    upstream_w.write(data)
                    transfer_stats.bytes_upstream += len(data)
                    await upstream_w.drain()
                    transfer_stats.log_transfer_update()
            except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
                log.warning("[%s] %s → %s: client-to-upstream error: %s", request_id, addr, hostname, e)
            finally:
                try:
                    upstream_w.write_eof()
                except OSError as e:
                    log.warning("[%s] %s → %s: error sending EOF to upstream: %s", request_id, addr, hostname, e)
                
        await asyncio.gather(
            client_to_upstream(),
            relay(upstream_r, client_w, transfer_stats),
        )

    except (ConnectionError, OSError) as e:
        log.error("[%s] %s: %s", request_id, addr, e)
    finally:
        # Log connection close with duration if connection was established
        if 'hostname' in locals() and 'start_time' in locals():
            duration = asyncio.get_event_loop().time() - start_time
            log.info("[%s] %s → %s: connection closed after %.2fs", request_id, addr, hostname, duration)
        client_w.close()
