"""
SNI Pass-Through Proxy + Static File Server + DNS Server (asyncio)
==================================================================
Port 443 : TLS pass-through, routed by SNI hostname
Port 80  : Plain HTTP static file server
Port 53  : DNS server — answers all A queries with the server's IP
"""

import asyncio
import logging
import os
import threading

from wonderwall.dns import run_dns_server
from wonderwall.http_proxy import run_static_server
from wonderwall.https_proxy import handle_tls
from wonderwall.logging_configuration import setup_logger

log = logging.getLogger(__name__)

PROXY_PORT = 443
DNS_A_RECORD_IP = os.getenv("DNS_A_RECORD_IP", None)


async def main():
    """Start the SNI proxy, DNS server, and static HTTP server."""
    setup_logger()
    if not DNS_A_RECORD_IP:
        raise ValueError("DNS_A_RECORD_IP environment variable is required")

    server = await asyncio.start_server(handle_tls, "0.0.0.0", PROXY_PORT)
    log.info("SNI proxy on :%d", PROXY_PORT)

    # DNS and static servers run in their own threads — both blocking but lightweight
    threading.Thread(target=run_dns_server, daemon=True).start()
    threading.Thread(target=run_static_server, daemon=True).start()

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
