"""
SNI Pass-Through Proxy + Static File Server + DNS Server (asyncio)
==================================================================
Port 443 : TLS pass-through, routed by SNI hostname
Port 80  : Plain HTTP static file server
Port 53  : DNS server — answers all A queries with the server's IP
"""

import asyncio
import contextlib
import logging
import os
import threading

from wonderwall.dns import DNS_PORT, run_dns_server
from wonderwall.http_proxy import HTTP_PORT, run_static_server
from wonderwall.https_proxy import handle_tls
from wonderwall.logging_configuration import setup_logger
from wonderwall.port_forward import FORWARD_PORTS, start_port_forwarders

log = logging.getLogger(__name__)

PROXY_PORT = 443
DNS_A_RECORD_IP = os.getenv("DNS_A_RECORD_IP", None)


async def main():
    """Start the SNI proxy, DNS server, static HTTP server, and any configured port forwarders."""
    setup_logger()
    if not DNS_A_RECORD_IP:
        raise ValueError("DNS_A_RECORD_IP environment variable is required")

    reserved_ports = {"SNI proxy": PROXY_PORT, "DNS server": DNS_PORT, "HTTP proxy": HTTP_PORT}
    for listen_port, _ in FORWARD_PORTS:
        for name, reserved_port in reserved_ports.items():
            if listen_port == reserved_port:
                raise ValueError(
                    f"FORWARD_PORTS listen port {listen_port} collides with the {name} port ({reserved_port})"
                )

    server = await asyncio.start_server(handle_tls, "0.0.0.0", PROXY_PORT)
    log.info("SNI proxy on :%d", PROXY_PORT)
    forward_servers = await start_port_forwarders()

    # DNS and static servers run in their own threads — both blocking but lightweight
    threading.Thread(target=run_dns_server, daemon=True).start()
    threading.Thread(target=run_static_server, daemon=True).start()

    all_servers = [server, *forward_servers]
    async with contextlib.AsyncExitStack() as stack:
        for s in all_servers:
            await stack.enter_async_context(s)
        await asyncio.gather(*(s.serve_forever() for s in all_servers))


if __name__ == "__main__":
    asyncio.run(main())
