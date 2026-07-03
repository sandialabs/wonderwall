"""Helpers for routing wonderwall's own outbound connections through a configured upstream forward proxy."""

import os

_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
_NO_PROXY_ENV_VARS = ("NO_PROXY", "no_proxy")


def _first_nonempty_env(names: tuple[str, ...], env: dict[str, str]) -> str | None:
    """Return the value of the first name in names with a non-empty value in env, or None."""
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


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


PROXY_URL = _get_proxy_url()  # None = no forward proxy configured
NO_PROXY = _parse_no_proxy(_first_nonempty_env(_NO_PROXY_ENV_VARS, os.environ))  # bypass hostnames/suffixes
