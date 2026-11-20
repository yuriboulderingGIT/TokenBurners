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


def test_different_portals_do_not_wait_for_each_other() -> None:
    async def run() -> float:
        f = _fetcher(1.0)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(f.get(f"p{i}", "https://a.test/") for i in range(5)))
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
