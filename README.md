# Wonderwall

Wonderwall is a single container that runs three network services together:

- **SNI proxy** (`:443`) — passes through TLS traffic to upstream servers based on hostname, without decrypting it
- **DNS server** (`:53`) — resolves all A-record queries to a single configurable IP
- **HTTP proxy** (`:80`) — proxies HTTP traffic to upstream servers, optionally serving static files for a specific domain

Together these services let you stand up a self-contained network environment where all hostnames resolve to your container and TLS traffic is routed transparently to real upstreams.

## How It Works

### SNI Proxy (`:443`)

Reads the TLS ClientHello packet to extract the SNI hostname, then opens a connection to the same hostname on the upstream port and relays bytes in both directions — no decryption, no certificate involved. Connections can be restricted to a hostname allowlist and a specific domain can be reserved for local static content instead of proxying.

### DNS Server (`:53`)

Returns `DNS_A_RECORD_IP` for every A-record query, causing all hostnames to resolve to the container. Other record types receive an empty NOERROR response.

If `INTERNAL_SUBNET` is set, the server first attempts to resolve the queried hostname using the system resolver. If any resolved address falls within that subnet, the real address is returned instead of `DNS_A_RECORD_IP`, allowing clients to reach internal services directly without being proxied.

### HTTP Proxy (`:80`)

Forwards HTTP requests to the upstream host named in the `Host` header. If `STATIC_DOMAIN` is set, requests for that domain are served from `STATIC_DIR` instead of being proxied; that domain is also excluded from TLS proxying. If `STATIC_DOMAIN` is not set, all requests are proxied. Proxied domains can be restricted to an allowlist via `ALLOWED_HOSTS`.

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|---|---|---|
| `DNS_A_RECORD_IP` | *(required)* | IP address returned for all DNS A-record queries |
| `INTERNAL_SUBNET` | *(unset)* | CIDR subnet (e.g. `172.128.0.0/24`). When set, A-record queries that resolve to an address within this subnet return the real address instead of `DNS_A_RECORD_IP` |
| `DNS_PORT` | `53` | DNS listening port |
| `HTTP_PORT` | `80` | HTTP proxy listening port |
| `STATIC_DIR` | `./static` | Directory to serve over HTTP when `STATIC_DOMAIN` is set |
| `STATIC_DOMAIN` | *(system hostname)* | Requests for this domain are served as static files; all other HTTP requests are proxied. If unset, all HTTP traffic is proxied. Also excluded from TLS proxying. |
| `ALLOWED_HOSTS` | *(allow all)* | Comma-separated wildcard patterns; only matching hostnames are proxied (applies to both HTTP and TLS). `*.example.com` matches subdomains at any depth; `pre*.example.com` matches within a single label only. |
| `UPSTREAM_PORT` | `443` | Port used when connecting to upstream TLS servers |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

## Running with Docker

```sh
docker build -t wonderwall .
docker run \
  -e DNS_A_RECORD_IP=<your-container-ip> \
  -p 53:53/udp \
  -p 80:80 \
  -p 443:443 \
  wonderwall
```

Static files can be mounted into the container:

```sh
docker run \
  -e DNS_A_RECORD_IP=<your-container-ip> \
  -v /path/to/files:/app/static \
  -p 53:53/udp -p 80:80 -p 443:443 \
  wonderwall
```

## Docker Compose Example

`docker-compose.yml` is an example that places wonderwall on an isolated internal network with a `curl` sidecar configured to use it as its DNS resolver.

```sh
docker compose up
docker compose exec curl curl https://example.com
```

The compose file accepts two variables:

| Variable | Default | Description |
|---|---|---|
| `WONDERWALL_IP` | `172.128.0.128` | IP assigned to the wonderwall container |
| `INTERNAL_SUBNET` | `172.128.0.0/24` | Subnet for the isolated internal network |

## Development

Dependencies are managed with [Poetry](https://python-poetry.org/).

```sh
poetry install
poetry run pytest
```
