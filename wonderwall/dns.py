"""DNS server — answers all A queries with a configured IP."""

import ipaddress
import logging
import os
import re
import socket

from nserver import A, NameServer, Query
from nserver.application import DirectApplication
from nserver.rules import ALL_QTYPES
from nserver.transport import UDPv4Transport

from wonderwall.https_proxy import _parse_allowed_hosts

log = logging.getLogger(__name__)

DNS_PORT = int(os.getenv("DNS_PORT", "53"))
DNS_A_RECORD_IP = os.getenv("DNS_A_RECORD_IP", None)
INTERNAL_SUBNET = os.getenv("INTERNAL_SUBNET", None)
ALLOWED_HOSTS = _parse_allowed_hosts(os.getenv("ALLOWED_HOSTS"))


def _resolve_a(
    name: str, internal_network, fallback_ip: str, allowed_hosts=None
) -> str | int | None:
    """Return the IP for an A record: internal address if it falls in internal_network, else fallback_ip.

    If allowed_hosts is configured, only resolve hosts that match the allowed patterns.
    Returns None for non-allowed hosts.
    """
    # Check if host is allowed (if allowed_hosts is configured)
    is_allowed = allowed_hosts is None or any(
        p.fullmatch(name) for p in allowed_hosts
    )

    # If no internal network is configured, return fallback for allowed hosts
    if internal_network is None:
        if is_allowed:
            log.debug("Host %s is allowed, returning fallback %s", name, fallback_ip)
            return fallback_ip
        else:
            log.debug("Host %s is not allowed, returning None", name)
            return None

    try:
        results = socket.getaddrinfo(name, None, socket.AF_INET)
        ips = [r[4][0] for r in results]
        log.debug("Resolved %s to %s", name, ips)
        for ip in ips:
            if ipaddress.ip_address(ip) in internal_network:
                log.debug(
                    "%s is in %s, returning internal IP %s", ip, internal_network, ip
                )
                return ip
    except socket.gaierror as e:
        log.debug("Failed to resolve %s: %s", name, e)
        # If DNS resolution fails and host is allowed, return fallback
        if is_allowed:
            log.debug("Host %s is allowed, DNS resolution failed, returning fallback %s", name, fallback_ip)
            return fallback_ip
        return None

    # No internal IPs found - check if host is allowed
    if is_allowed:
        log.debug(
            "Host %s is allowed, No resolved IPs in %s, returning fallback %s",
            name,
            internal_network,
            fallback_ip,
        )
        return fallback_ip
    else:
        log.debug(
            "Host %s is not allowed, No resolved IPs in %s, returning None",
            name,
            internal_network,
        )
        return None


def _make_catch_all_a(internal_network, fallback_ip, allowed_hosts=None):
    """Return a handler that resolves A queries, preferring internal IPs when configured.

    If allowed_hosts is configured, only resolves hosts that match the allowed patterns.
    Returns None for non-allowed hosts, resulting in empty NOERROR response.
    """

    def catch_all_a(query: Query):
        log.info("Got query for %s", query.name)
        ip = _resolve_a(query.name, internal_network, fallback_ip, allowed_hosts)
        if ip is None:
            log.info(
                "Not returning A record for %s (not allowed or resolution failed)",
                query.name,
            )
            return None
        return A(query.name, ip)

    return catch_all_a


def _catch_all_other(_: Query):
    """Return NOERROR with an empty answer for any non-A query type."""
    return None  # NOERROR with empty answer — domain exists, record type unsupported


def run_dns_server():
    """Start the UDP DNS server and block until it exits."""
    ns = NameServer("wonderwall")
    internal_network = (
        ipaddress.ip_network(INTERNAL_SUBNET, strict=False) if INTERNAL_SUBNET else None
    )
    if internal_network is None:
        log.info(
            "INTERNAL_SUBNET not configured, all DNS queries will resolve to DNS_A_RECORD_IP (%s)",
            DNS_A_RECORD_IP,
        )

    if ALLOWED_HOSTS is not None:
        log.info(
            "ALLOWED_HOSTS configured, only resolving DNS queries for allowed hosts: %s",
            os.getenv("ALLOWED_HOSTS"),
        )

    ns.rule(re.compile(r".*"), ["A"])(
        _make_catch_all_a(internal_network, DNS_A_RECORD_IP, ALLOWED_HOSTS)
    )
    ns.rule(re.compile(r".*"), ALL_QTYPES)(_catch_all_other)

    app = DirectApplication(ns, UDPv4Transport("0.0.0.0", DNS_PORT))
    log.info("DNS server on :%d, resolving A queries to %s", DNS_PORT, DNS_A_RECORD_IP)
    app.run()
