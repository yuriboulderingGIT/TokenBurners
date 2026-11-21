"""Offline destination policy checks. Both DNS and the underlying socket are fixtures."""

import asyncio
import socket

import httpcore
import httpx
import pytest

from app.destinations import DestinationDenied, PublicNetworkBackend, _is_allowed
from app.fetcher import Fetcher


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.1.1", "100.64.1.1", "224.0.0.1",
    "168.63.129.16", "::1", "::ffff:10.0.0.1", "fe80::1", "fc00::1", "fec0::1", "ff02::1",
    "2002:abcd::1", "64:ff9b::1",
])
def test_public_policy_denies_nonpublic_destinations(address):
    assert not _is_allowed(address, mock=False)


def test_socket_uses_checked_address_and_http_keeps_original_host(monkeypatch):
    async def exercise():
        connected = []
        names = []
        requests = bytearray()
        answers = iter([["1.1.1.1"], ["1.1.1.1", "10.0.0.1"]])

        async def resolve(name, port, **kwargs):
            assert (name, port) == ("portal.example.com", 443)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)) for ip in next(answers)]

        class Stream(httpcore.AsyncMockStream):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                names.append(server_hostname)
                return self

            async def write(self, buffer, timeout=None):
                requests.extend(buffer)

        async def dial(self, address, port, *args):
            connected.append((address, port))
            return Stream([b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\nok"])

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
        monkeypatch.setattr("app.fetcher.PUBLIC_DESTINATIONS_ONLY", True)
        fetcher = Fetcher(min_interval=0)
        try:
            assert (await fetcher.get("p", "https://portal.example.com/list")).text == "ok"
            with pytest.raises(DestinationDenied, match="restricted"):
                await fetcher.get("p", "https://portal.example.com/list")
        finally:
            await fetcher.aclose()
        assert connected == [("1.1.1.1", 443)]
        assert names == ["portal.example.com"]
        assert b"host: portal.example.com\r\n" in requests.lower()

    asyncio.run(exercise())


def test_files_searches_and_redirects_use_checked_connections(monkeypatch, tmp_path):
    async def exercise():
        connected = []

        async def dial(self, address, port, *args):
            connected.append(address)
            return httpcore.AsyncMockStream([
                b"HTTP/1.1 302 Found\r\nLocation: http://10.0.0.1/fixture\r\nContent-Length: 0\r\n\r\n",
            ])

        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
        monkeypatch.setattr("app.fetcher.PUBLIC_DESTINATIONS_ONLY", True)
        fetcher = Fetcher(min_interval=0)
        try:
            with pytest.raises(DestinationDenied):
                await fetcher.get("p", "http://1.1.1.1/")
            with pytest.raises(DestinationDenied):
                await fetcher.stream("p", "http://10.0.0.1/fixture", 500, tmp_path)
            with pytest.raises(DestinationDenied):
                await fetcher.public_search("p", "http://10.0.0.1/search", media_type="application/json",
                                            content=b"{}", cookies=httpx.Cookies())
        finally:
            await fetcher.aclose()
        assert connected == ["1.1.1.1"]
        assert list(tmp_path.iterdir()) == []

    asyncio.run(exercise())


def test_unreachable_address_leaves_time_for_another_public_address(monkeypatch):
    async def exercise():
        attempts = []

        async def resolve(name, port, **kwargs):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", port)),
            ]

        async def dial(self, address, port, timeout, *args):
            attempts.append((address, timeout))
            if len(attempts) == 1:
                raise httpcore.ConnectTimeout()
            return httpcore.AsyncMockStream([])

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
        stream = await PublicNetworkBackend("").connect_tcp("portal.example.com", 443, timeout=30)
        await stream.aclose()
        assert [ip for ip, _ in attempts] == ["2606:4700:4700::1111", "1.1.1.1"]
        assert 0 < attempts[0][1] <= 15 < attempts[1][1] <= 30

    asyncio.run(exercise())


def test_local_mock_exception_matches_one_host_and_port(monkeypatch):
    async def exercise():
        connected = []

        async def resolve(name, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", port))]

        async def dial(self, address, port, *args):
            connected.append((address, port))
            return httpcore.AsyncMockStream([])

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
        backend = PublicNetworkBackend("mock:8090")
        await (await backend.connect_tcp("mock", 8090)).aclose()
        for name, port in [("mock", 8080), ("other", 8090), ("other.example.com", 8090)]:
            with pytest.raises(DestinationDenied):
                await backend.connect_tcp(name, port)
        assert connected == [("10.0.0.1", 8090)]
        assert not _is_allowed("169.254.1.1", mock=True)

    asyncio.run(exercise())


def test_private_portals_are_reachable_unless_public_destinations_only(monkeypatch):
    # Test portals may run on a private network, so the address check is opt-in.
    from app.destinations import PublicHTTPTransport

    async def exercise():
        fetcher = Fetcher(min_interval=0)
        try:
            assert not isinstance(fetcher._client._transport, PublicHTTPTransport)
        finally:
            await fetcher.aclose()
        monkeypatch.setattr("app.fetcher.PUBLIC_DESTINATIONS_ONLY", True)
        fetcher = Fetcher(min_interval=0)
        try:
            assert isinstance(fetcher._client._transport, PublicHTTPTransport)
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
