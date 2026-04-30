"""Shared test helpers."""

import struct


def build_client_hello(sni: str | None = None, with_extensions: bool = True) -> bytes:
    """Build a minimal but structurally valid TLS 1.2 ClientHello packet."""
    random_bytes = b"\xab" * 32
    session_id = b"\x00"  # empty session ID (length=0)
    cipher_suites = struct.pack("!H", 2) + b"\x00\x2f"  # TLS_RSA_WITH_AES_128_CBC_SHA
    compression = b"\x01\x00"  # one method: null

    extensions_data = b""
    if sni is not None:
        sni_bytes = sni.encode("ascii")
        # SNI extension body: list_length(2) + name_type(1) + name_length(2) + name
        sni_ext_body = struct.pack("!H", len(sni_bytes) + 3)
        sni_ext_body += b"\x00"  # name_type: host_name
        sni_ext_body += struct.pack("!H", len(sni_bytes))
        sni_ext_body += sni_bytes
        # Extension: type(2) + length(2) + body
        extensions_data += struct.pack("!HH", 0x0000, len(sni_ext_body)) + sni_ext_body

    body = b"\x03\x03" + random_bytes + session_id + cipher_suites + compression
    if with_extensions:
        body += struct.pack("!H", len(extensions_data)) + extensions_data

    # Handshake: type(1) + length(3) + body
    hs_len = struct.pack("!I", len(body))[1:]  # 3-byte big-endian length
    handshake = b"\x01" + hs_len + body

    # TLS record: content_type(1) + version(2) + length(2) + handshake
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
