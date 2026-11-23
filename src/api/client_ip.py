"""Client address resolution that trusts X-Forwarded-For only from known proxies."""

from __future__ import annotations

from functools import lru_cache
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

from starlette.requests import Request

from src.core.config import settings

_Network = IPv4Network | IPv6Network


@lru_cache
def _networks(entries: tuple[str, ...]) -> tuple[_Network, ...]:
    return tuple(ip_network(entry, strict=False) for entry in entries)


def _address(host: str) -> IPv4Address | IPv6Address:
    """Parse a host, reading an IPv4-mapped IPv6 address as its IPv4 address."""
    address = ip_address(host)
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _trusted(host: str, networks: tuple[_Network, ...]) -> bool:
    try:
        address = _address(host)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip(request: Request) -> str:
    """Return the address of the caller.

    The socket peer is the client unless it is in ``TRUSTED_PROXIES`` (empty by
    default). Then ``X-Forwarded-For`` is walked right to left, because each
    proxy appends the address it received the request from, and the first hop
    that is not itself a trusted proxy is the client (the peer if every hop is
    trusted). Anything to the left of
    that hop is caller-supplied and is never read. A malformed hop is not
    believed; the peer address is used instead.
    """
    peer = request.client.host if request.client else "unknown"
    networks = _networks(tuple(settings.TRUSTED_PROXIES))
    if not networks or not _trusted(peer, networks):
        return peer

    header = ",".join(request.headers.getlist("x-forwarded-for"))
    hops = [hop.strip() for hop in header.split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            address = str(_address(hop))
        except ValueError:
            return peer
        if not _trusted(address, networks):
            return address
    return peer
