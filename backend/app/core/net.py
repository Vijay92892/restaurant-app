"""Client network metadata.

`X-Forwarded-For` is attacker-controlled unless it was written by our own proxy, so it is only
honoured when the direct peer is a configured trusted proxy, and it is walked right-to-left,
skipping our own proxies, to find the first untrusted hop.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from functools import lru_cache

from starlette.requests import HTTPConnection

UNKNOWN_IP = "0.0.0.0"  # noqa: S104 - sentinel value, not a bind address


@lru_cache(maxsize=32)
def _networks(
    trusted: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(item, strict=False) for item in trusted)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted: tuple[str, ...]) -> str:
    peer_ip = _parse_ip(peer or "")
    if peer_ip is None:
        return UNKNOWN_IP
    networks = _networks(trusted)

    def is_trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(ip in net for net in networks)

    if not networks or not is_trusted(peer_ip) or not forwarded_for:
        return str(peer_ip)
    hops = [h for h in (part.strip() for part in forwarded_for.split(",")) if h]
    for hop in reversed(hops[-20:]):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            break
        if not is_trusted(hop_ip):
            return str(hop_ip)
    return str(peer_ip)


@dataclass(frozen=True)
class RequestMeta:
    ip: str
    user_agent: str | None
    request_id: str | None


def request_meta(conn: HTTPConnection, trusted_proxies: list[str]) -> RequestMeta:
    peer = conn.client.host if conn.client else None
    ip = resolve_client_ip(peer, conn.headers.get("x-forwarded-for"), tuple(trusted_proxies))
    user_agent = conn.headers.get("user-agent")
    return RequestMeta(
        ip=ip,
        user_agent=user_agent[:200] if user_agent else None,
        request_id=getattr(conn.state, "request_id", None),
    )
