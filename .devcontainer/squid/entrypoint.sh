#!/bin/sh
set -e

CA_DIR=/etc/squid/ssl_cert
CA_KEY="$CA_DIR/squid-ca-key.pem"
CA_CERT="$CA_DIR/squid-ca-cert.pem"
CA_BUNDLE="$CA_DIR/squid-ca.pem"
PUBLIC_DIR=/etc/squid/ca-public
SSL_DB=/var/lib/squid/ssl_db
LOG_DIR=/var/log/squid
ACCESS_LOG="$LOG_DIR/access.log"
CACHE_LOG="$LOG_DIR/cache.log"

mkdir -p "$CA_DIR" "$PUBLIC_DIR" "$LOG_DIR"

# Generate the interception CA once; persisted in a volume so rebuilding this
# image doesn't rotate the CA and break trust already established elsewhere.
if [ ! -f "$CA_BUNDLE" ]; then
    echo "Generating self-signed squid interception CA..."
    openssl req -new -newkey rsa:2048 -sha256 -days 3650 -nodes -x509 \
        -keyout "$CA_KEY" -out "$CA_CERT" \
        -subj "/CN=wonderwall-devcontainer-squid-ca/O=wonderwall-dev"
    cat "$CA_CERT" "$CA_KEY" > "$CA_BUNDLE"
fi

# Publish just the public cert to the volume shared with the app container.
cp "$CA_CERT" "$PUBLIC_DIR/squid-ca.crt"

# Initialize the dynamic-cert cache database on first run. $SSL_DB itself
# must not exist beforehand: security_file_certgen -c does its own mkdir()
# on this exact path and fails if it's already there. That's why the
# squid-ssl-db volume is mounted at the parent (/var/lib/squid) rather than
# at $SSL_DB directly - a volume mounted at $SSL_DB would make it a
# permanent, un-removable mount point and this init would fail every time.
if [ ! -f "$SSL_DB/index.txt" ]; then
    echo "Initializing squid ssl_db..."
    rm -rf "$SSL_DB"
    /usr/lib/squid/security_file_certgen -c -s "$SSL_DB" -M 4MB
fi

chown -R proxy:proxy "$CA_DIR" "$PUBLIC_DIR" "$SSL_DB"

# Squid logs to real files (see squid.conf) instead of /dev/stdout directly,
# since it can't open /dev/stdout as the unprivileged proxy user. Tail those
# files into the container's actual stdout here, while still root.
touch "$ACCESS_LOG" "$CACHE_LOG"
chown proxy:proxy "$ACCESS_LOG" "$CACHE_LOG"
tail -F "$ACCESS_LOG" "$CACHE_LOG" &

exec squid -N -d 1
