FROM ubuntu:24.04

# squid-openssl is the Ubuntu squid build compiled with OpenSSL support,
# required for ssl-bump interception. The plain "squid" package lacks it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    squid-openssl \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
