"""TCP port forwarding to a configurable host (default: host.docker.internal)."""

import asyncio
import functools
import logging
import os
import uuid

from wonderwall.https_proxy import relay
from wonderwall.transfer_stats import TransferStats

log = logging.getLogger(__name__)

FORWARD_TARGET_HOST = os.getenv("FORWARD_TARGET_HOST", "host.docker.internal")


def _parse_port_forwards(env_val: str | None) -> list[tuple[int, int]]:
    """Parse FORWARD_PORTS into a list of (listen_port, target_port) pairs.

    Each comma-separated entry is either "PORT" (forwards PORT -> PORT) or
    "LISTEN_PORT:TARGET_PORT". Malformed entries (non-integer, wrong number of
    colon-separated fields, out of the 1-65535 range) and duplicate listen
    ports are logged as warnings and skipped rather than raising -- mirrors
    the tolerant parsing style of _parse_allowed_hosts.
    """
    if not env_val:
        return []

    seen_listen_ports: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for raw_entry in env_val.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue

        fields = entry.split(":")
        if len(fields) not in (1, 2):
            log.warning("Skipping malformed FORWARD_PORTS entry %r: expected PORT or LISTEN:TARGET", entry)
            continue

        try:
            listen_port = int(fields[0].strip())
            target_port = int(fields[1].strip()) if len(fields) == 2 else listen_port
        except ValueError:
            log.warning("Skipping malformed FORWARD_PORTS entry %r: ports must be integers", entry)
            continue

        if not (1 <= listen_port <= 65535 and 1 <= target_port <= 65535):
            log.warning("Skipping malformed FORWARD_PORTS entry %r: ports must be in 1-65535", entry)
            continue

        if listen_port in seen_listen_ports:
            log.warning("Skipping duplicate FORWARD_PORTS listen port %d in entry %r", listen_port, entry)
            continue

        seen_listen_ports.add(listen_port)
        pairs.append((listen_port, target_port))

    return pairs


FORWARD_PORTS = _parse_port_forwards(os.getenv("FORWARD_PORTS"))


async def _handle_forward_connection(
    listen_port: int,
    target_port: int,
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
) -> None:
    """Relay a single accepted connection to (FORWARD_TARGET_HOST, target_port).

    Connects directly via asyncio.open_connection rather than through
    proxy_config.open_upstream_connection -- that machinery routes wonderwall's
    own internet egress through a configured corporate forward proxy, whereas
    FORWARD_TARGET_HOST is a local Docker-host route where that would be
    semantically wrong.
    """
    addr = client_w.get_extra_info("peername")
    request_id = str(uuid.uuid4())[:8]
    target_label = f"{FORWARD_TARGET_HOST}:{target_port}"

    try:
        target_r, target_w = await asyncio.open_connection(FORWARD_TARGET_HOST, target_port)
    except (ConnectionError, OSError) as e:
        log.warning("[%s] :%d %s: could not connect to %s: %s", request_id, listen_port, addr, target_label, e)
        client_w.close()
        return

    log.info("[%s] %s → :%d → %s", request_id, addr, listen_port, target_label)
    transfer_stats = TransferStats(request_id, addr, target_label)

    async def client_to_target():
        try:
            while data := await client_r.read(4096):
                target_w.write(data)
                transfer_stats.bytes_upstream += len(data)
                await target_w.drain()
                transfer_stats.log_transfer_update()
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
            log.warning("[%s] %s → :%d → %s: client-to-target error: %s", request_id, addr, listen_port, target_label, e)
        finally:
            try:
                target_w.write_eof()
            except OSError as e:
                log.warning("[%s] %s → :%d → %s: error sending EOF to target: %s", request_id, addr, listen_port, target_label, e)

    await asyncio.gather(
        client_to_target(),
        relay(target_r, client_w, transfer_stats),
    )
    client_w.close()


async def start_port_forwarders() -> list[asyncio.base_events.Server]:
    """Start one TCP listener per FORWARD_PORTS entry, forwarding to FORWARD_TARGET_HOST.

    Returns the list of started servers without calling serve_forever() --
    the caller is responsible for driving them alongside wonderwall's other
    asyncio servers. Returns [] if no ports are configured.
    """
    servers = []
    for listen_port, target_port in FORWARD_PORTS:
        handler = functools.partial(_handle_forward_connection, listen_port, target_port)
        server = await asyncio.start_server(handler, "0.0.0.0", listen_port)
        log.info("Port forward :%d -> %s:%d", listen_port, FORWARD_TARGET_HOST, target_port)
        servers.append(server)
    return servers
