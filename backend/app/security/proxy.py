"""Trusted-proxy client-IP resolution (P3a; used by P3b limits).

Selection rule: X-Forwarded-For is honoured only when the direct
peer is itself inside TRUSTED_PROXIES. The chain is walked from
the right (nearest hop) leftwards, skipping trusted addresses;
the first untrusted address is the effective client. If every
hop is trusted, the leftmost address is the client. An untrusted
peer's X-Forwarded-For is ignored entirely, and a malformed chain
fails safe to the direct peer. A missing peer yields None.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(address, networks: tuple) -> bool:
    return any(address in network for network in networks)


def effective_client_ip(request: Any, trusted_networks: Iterable) -> str | None:
    """Effective client IP for a request, or None without a peer."""
    networks = tuple(trusted_networks)
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) if client is not None else None
    if not peer:
        return None
    peer_address = _parse_ip(peer)
    if peer_address is None or not _is_trusted(peer_address, networks):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    parsed = [_parse_ip(hop) for hop in hops]
    if not hops or any(address is None for address in parsed):
        return peer
    for address in reversed(parsed):
        if not _is_trusted(address, networks):
            return str(address)
    return str(parsed[0])
