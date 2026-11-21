"""The 1 request/second/portal rule must always hold. OWNER: A1."""

import asyncio

import httpx
import pytest

from app.fetcher import FakeFetcher, Fetcher, PortalCancelled


def _fetcher(interval: float) -> Fetcher:
    return Fetcher(min_interval=interval, transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_requests_to_one_portal_are_spaced() -> None:
    async def run() -> list[float]:
        f = _fetcher(0.2)
        await asyncio.gather(*(f.get("p1", f"https://a.test/{i}") for i in range(4)))
        await f.aclose()
        return [t for _, t, _ in f.request_log]

    times = sorted(asyncio.run(run()))
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.19 for g in gaps), gaps


def test_different_portals_on_different_hosts_do_not_wait_for_each_other() -> None:
    async def run() -> float:
        f = _fetcher(1.0)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(f.get(f"p{i}", f"https://{i}.test/") for i in range(5)))
        await f.aclose()
        return loop.time() - start

    assert asyncio.run(run()) < 0.9


def test_cancelled_portal_stops() -> None:
    async def run() -> None:
        f = _fetcher(0.0)
        f.cancel("p1")
        await f.for_portal("p1").get("https://a.test/")

    with pytest.raises(PortalCancelled):
        asyncio.run(run())


def test_fake_fetcher_serves_pages() -> None:
    async def run() -> tuple[int, str, int]:
        f = FakeFetcher({"https://a.test/list": "<html>ok</html>"})
        ok = await f.get("p", "https://a.test/list")
        missing = await f.get("p", "https://a.test/other")
        await f.aclose()
        return ok.status_code, ok.text, missing.status_code

    assert asyncio.run(run()) == (200, "<html>ok</html>", 404)


def test_redirect_hops_each_wait_their_turn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://a.test/new"})
        return httpx.Response(200, text="here")

    async def run() -> tuple[str, list[float]]:
        f = Fetcher(min_interval=0.2, transport=httpx.MockTransport(handler))
        r = await f.get("p1", "https://a.test/old")
        await f.aclose()
        return r.text, [t for _, t, _ in f.request_log]

    text, times = asyncio.run(run())
    assert text == "here"
    assert len(times) == 2 and times[1] - times[0] >= 0.19


def test_two_portals_share_destination_host_even_across_schemes_and_ports() -> None:
    async def run():
        f = _fetcher(1.0)
        try:
            await asyncio.gather(
                f.get("one", "http://A.test:8080/list"),
                f.for_portal("two").wait_turn("https://a.test/docs"),
            )
            return [t for _, t, _ in f.request_log]
        finally:
            await f.aclose()

    first, second = asyncio.run(run())
    assert second - first >= 1.0


def test_one_portal_remains_spaced_across_hosts() -> None:
    async def run():
        f = _fetcher(0.1)
        try:
            await asyncio.gather(f.get("p", "https://a.test"), f.get("p", "https://b.test"))
            return [t for _, t, _ in f.request_log]
        finally:
            await f.aclose()

    first, second = asyncio.run(run())
    assert second - first >= 0.1


def test_slow_initial_connection_cannot_bunch_redirect_and_browser_requests():
    async def exercise():
        arrivals = []

        async def respond(request):
            if request.url.path == "/entry":
                await asyncio.sleep(0.08)  # connection setup before the portal sees the request
            arrivals.append(asyncio.get_running_loop().time())
            if request.url.path == "/entry":
                return httpx.Response(302, headers={"Location": "/listing"})
            return httpx.Response(200)

        fetcher = Fetcher(min_interval=0.05, transport=httpx.MockTransport(respond))
        try:
            await fetcher.get("p", "https://buyer.example/entry")
            await fetcher.for_portal("p").get("https://cdn.example/app.js")
            assert all(b-a >= 0.05 for a, b in zip(arrivals, arrivals[1:]))
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
