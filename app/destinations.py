"""Destination checks for outgoing portal traffic, applied at TCP connection time."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

from app.config import MOCK_PORTAL_ADDRESS


class DestinationDenied(httpx.RequestError):
    """Permanent policy decision, including when a formerly public name changes address."""


_MOCK_NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)))
# Azure exposes a host-local platform service on an otherwise globally numbered address.
_AZURE_PLATFORM = ipaddress.ip_address("168.63.129.16")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".example", ".invalid", ".test")


def _is_allowed(address: str, mock: bool) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return False
        ip = ip.ipv4_mapped or ip
    if mock and (ip.is_loopback or any(ip in network for network in _MOCK_NETWORKS)):
        return True
    if isinstance(ip, ipaddress.IPv6Address) and (ip.is_site_local or ip.sixtofour or ip.teredo):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and ip != _AZURE_PLATFORM


def _mock_endpoint(value: str) -> tuple[str, int] | None:
    if not value:
        return None
    parts = urlsplit("//" + value)
    if (not parts.hostname or not parts.port or parts.username is not None
            or parts.path or parts.query or parts.fragment or "*" in parts.hostname):
        raise ValueError("MOCK_PORTAL_ADDRESS must be one host and port")
    return parts.hostname.rstrip("."), parts.port


async def _resolve(host: str, port: int, mock: bool) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    name = host.lower().rstrip(".")
    if not mock and ("." not in name or name.endswith(_LOCAL_SUFFIXES)):
        raise DestinationDenied("portal destination must be a public Internet host")
    records = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    return list(dict.fromkeys(record[4][0] for record in records))


class PublicNetworkBackend(httpcore.AnyIOBackend):
    def __init__(self, mock_address: str = MOCK_PORTAL_ADDRESS) -> None:
        self.mock_endpoint = _mock_endpoint(mock_address)

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None, socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        # httpcore pools by the original origin and handles TLS with its original
        # hostname. Only the socket gets the verified numeric address, never a second lookup.
        mock = (host.lower().rstrip("."), port) == self.mock_endpoint
        try:
            async with asyncio.timeout(timeout) as budget:
                addresses = await _resolve(host, port, mock)
                if not addresses:
                    raise httpcore.ConnectError("portal destination has no resolved address")
                if not all(_is_allowed(address, mock) for address in addresses):
                    raise DestinationDenied("portal destination resolves to a restricted address")
                for index, address in enumerate(addresses):
                    # Leave time to try IPv4 if, for example, an IPv6 route is unavailable.
                    attempt_timeout = None
                    if budget.when() is not None:
                        remaining = max(0.0, budget.when() - asyncio.get_running_loop().time())
                        attempt_timeout = remaining / (len(addresses) - index)
                    try:
                        return await super().connect_tcp(address, port, attempt_timeout, local_address, socket_options)
                    except (httpcore.ConnectError, httpcore.ConnectTimeout):
                        if index == len(addresses) - 1:
                            raise
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("portal connection timed out") from exc
        except socket.gaierror as exc:
            raise httpcore.ConnectError("could not resolve the portal destination") from exc


class PublicHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        # HTTPX's HTTP adapter supplies streaming and error translation. Its constructor
        # does not expose the network_backend option, so construct the pool here.
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=PublicNetworkBackend(), ssl_context=httpx.create_ssl_context(),
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=5,
        )
