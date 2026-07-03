"""Helpers for routing wonderwall's own outbound connections through a configured upstream forward proxy."""

import asyncio
import logging
import os
import socket
import time
import urllib.parse

log = logging.getLogger(__name__)

_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
_NO_PROXY_ENV_VARS = ("NO_PROXY", "no_proxy")
_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _first_nonempty_env(names: tuple[str, ...], env: dict[str, str]) -> str | None:
    """Return the value of the first name in names with a non-empty value in env, or None."""
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _env_flag(name: str, env: dict[str, str] | None = None) -> bool:
    """Return True if the named env var is set to a recognized truthy value (case-insensitive)."""
    value = (os.environ if env is None else env).get(name, "")
    return value.strip().lower() in _TRUTHY_VALUES


def _get_proxy_url(env: dict[str, str] | None = None) -> str | None:
    """Return the configured forward-proxy URL, or None if none is set.

    Checks HTTPS_PROXY, https_proxy, HTTP_PROXY, http_proxy in that order —
    uppercase before lowercase, https-related before http-related, matching
    curl's precedence — and returns the first non-empty value found. Unlike
    curl, this is a single flattened priority list rather than "pick a proxy
    per scheme": this deployment only ever has one upstream egress proxy, so
    there's no need to distinguish which scheme selected it.
    """
    return _first_nonempty_env(_PROXY_ENV_VARS, os.environ if env is None else env)


def _parse_no_proxy(env_val: str | None) -> list[str]:
    """Parse a NO_PROXY env var into a list of lowercased hostname/suffix entries.

    Entries are comma-separated; surrounding whitespace and an optional
    leading '.' are stripped (so '.example.com' and 'example.com' are
    equivalent). A bare '*' entry is passed through as-is — see
    _hostname_bypasses_proxy() for how it's handled. host:port entries are
    not specially parsed: the port stays part of the string and will simply
    fail to match a bare hostname later (port-aware matching is out of scope).
    """
    if not env_val:
        return []
    return [p.strip().lstrip(".").lower() for p in env_val.split(",") if p.strip()]


def _hostname_bypasses_proxy(hostname: str, no_proxy: list[str]) -> bool:
    """Return True if hostname should bypass the proxy per a parsed NO_PROXY list.

    Follows standard NO_PROXY semantics (as used by curl and most other
    tools): a bare '*' entry bypasses the proxy for all hosts; otherwise each
    entry matches the hostname exactly or as a domain suffix (e.g.
    'example.com' matches both 'example.com' and 'www.example.com').
    Matching is case-insensitive.
    """
    hostname = hostname.lower()
    return any(
        entry == "*" or hostname == entry or hostname.endswith("." + entry)
        for entry in no_proxy
    )


def _parse_proxy_host_port(proxy_url: str) -> tuple[str, int]:
    """Parse a forward-proxy URL into (host, port) for a plain TCP connection to the proxy.

    The URL's scheme is ignored: HTTPS_PROXY conventionally holds a plain http://
    URL meaning "speak plain HTTP CONNECT to this proxy", not "speak TLS to the
    proxy itself". TLS-to-proxy and proxy authentication (userinfo in the URL)
    are both out of scope for now. Defaults to port 80 if unspecified.
    """
    parts = urllib.parse.urlsplit(proxy_url)
    host = parts.hostname
    if not host:
        raise ConnectionError(f"invalid forward proxy URL: {proxy_url!r}")
    return host, parts.port or 80


async def _connect_via_proxy(
    proxy_host: str, proxy_port: int, target_host: str, target_port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP connection to the proxy and CONNECT-tunnel it to (target_host, target_port).

    Returns the proxy connection's own (reader, writer) pair once the tunnel is
    established -- from that point on it behaves exactly like a direct connection
    to the target for byte-relay purposes (no decryption happens here regardless).
    Raises ConnectionError if the proxy connection fails, the response is
    malformed/truncated, or the proxy does not respond 200 to the CONNECT.
    """
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    target = f"{target_host}:{target_port}"
    request = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii")
    writer.write(request)
    await writer.drain()

    try:
        response = await reader.readuntil(b"\r\n\r\n")
    except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
        writer.close()
        raise ConnectionError(
            f"no valid response from proxy {proxy_host}:{proxy_port} for CONNECT {target}: {e}"
        ) from e

    status_line = response.split(b"\r\n", 1)[0]
    status_parts = status_line.decode("latin-1", errors="replace").split(maxsplit=2)
    if len(status_parts) < 2 or status_parts[1] != "200":
        writer.close()
        raise ConnectionError(
            f"proxy {proxy_host}:{proxy_port} refused CONNECT {target}: {status_line!r}"
        )

    return reader, writer


async def open_upstream_connection(hostname: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a connection to (hostname, port) for TLS pass-through, routing through
    the configured forward proxy (PROXY_URL) via HTTP CONNECT unless hostname is
    bypassed by NO_PROXY.

    With no forward proxy configured (PROXY_URL is None, the default), this is
    exactly asyncio.open_connection(hostname, port) -- byte-for-byte identical to
    a direct connection, so existing direct-connect behavior is unaffected.

    If AUTO_PROXY is enabled and the proxy host itself can't be resolved in DNS,
    falls back to a direct connection instead of raising. Other proxy failures
    (refused, non-200 CONNECT, etc.) still raise regardless of AUTO_PROXY.

    While AUTO_PROXY is enabled and the proxy was recently found unresolvable,
    skips retrying it for AUTO_PROXY_RECHECK_SECONDS and connects directly
    instead -- avoids paying a failed DNS lookup on every connection while the
    proxy stays down, and automatically resumes using it once the window elapses.
    """
    global _proxy_unresolvable_until
    if PROXY_URL is None or _hostname_bypasses_proxy(hostname, NO_PROXY):
        return await asyncio.open_connection(hostname, port)
    proxy_host, proxy_port = _parse_proxy_host_port(PROXY_URL)
    if AUTO_PROXY:
        if time.monotonic() < _proxy_unresolvable_until:
            return await asyncio.open_connection(hostname, port)
        try:
            return await _connect_via_proxy(proxy_host, proxy_port, hostname, port)
        except socket.gaierror as e:
            _proxy_unresolvable_until = time.monotonic() + AUTO_PROXY_RECHECK_SECONDS
            log.warning(
                "AUTO_PROXY: proxy host %r could not be resolved (%s); falling back to a direct "
                "connection to %s and skipping the proxy for %ss",
                proxy_host, e, hostname, AUTO_PROXY_RECHECK_SECONDS,
            )
            return await asyncio.open_connection(hostname, port)
    return await _connect_via_proxy(proxy_host, proxy_port, hostname, port)


PROXY_URL = _get_proxy_url()  # None = no forward proxy configured
NO_PROXY = _parse_no_proxy(_first_nonempty_env(_NO_PROXY_ENV_VARS, os.environ))  # bypass hostnames/suffixes
AUTO_PROXY = _env_flag("AUTO_PROXY")  # if True, fall back to a direct connection when the proxy host can't be resolved
AUTO_PROXY_RECHECK_SECONDS = int(os.getenv("AUTO_PROXY_RECHECK_SECONDS", "30"))  # how long to skip a proxy found unresolvable
_proxy_unresolvable_until = 0.0  # monotonic timestamp; while now < this, AUTO_PROXY skips the proxy entirely
