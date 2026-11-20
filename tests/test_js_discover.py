"""Run a browser against an offline fixture, then replay its JSON feed."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from playwright.async_api import Error, async_playwright

from app.fetcher import FakeFetcher, Fetcher
from app.models import Health, Recipe
from brain import js_discover
from brain.js_discover import discover_json_api
from brain.learn import AttachFailed, learn_recipe
from brain.repair import repair
from brain.run_recipe import run_recipe

BASE = "http://portal.example"


def test_localized_records_outrank_settings_with_ids_and_titles():
    settings = {"items": [{"id": "primary", "title": "Brand color"}]}
    notices = {"results": [{"idweb": "26-112", "objet": "Travaux de voirie"}]}
    assert js_discover._feed_rank(notices, "/api/avis") > js_discover._feed_rank(settings, "/api/config")


class FixtureFetcher(FakeFetcher):
    async def get(self, portal_id, url, **kwargs):
        response = await super().get(portal_id, url, **kwargs)
        response.headers["content-type"] = "application/json" if "/api/" in url else "text/html; charset=utf-8"
        if url.endswith(".js"):
            response.headers["content-type"] = "application/javascript"
        return response


async def require_chromium():
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        await browser.close()


@pytest.mark.parametrize(("stage", "error", "reason"), [
    ("startup", PermissionError(), "could not start Playwright for browser discovery"),
    ("launch", Error("Executable doesn't exist at /browser"), "python -m playwright install chromium"),
    ("launch", Error("Access is denied"), "could not launch Chromium for browser discovery"),
])
def test_attach_reports_browser_runtime_failure(monkeypatch, stage, error, reason):
    @asynccontextmanager
    async def broken_playwright():
        if stage == "startup":
            raise error

        async def launch():
            raise error

        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    monkeypatch.setattr(js_discover, "async_playwright", broken_playwright)

    async def exercise():
        url = f"{BASE}/tenders"
        fetcher = FakeFetcher({url: '<html><body><script src="/bundle.js"></script></body></html>'})
        try:
            with pytest.raises(AttachFailed, match=reason):
                await learn_recipe(url, fetcher.for_portal("fixture"), None)
            assert [requested for _, _, requested in fetcher.request_log] == [url]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Selector loop cannot launch subprocesses")
def test_windows_selector_loop_errors_before_starting_playwright(monkeypatch):
    def unexpected_playwright():
        pytest.fail("Playwright should only start with Windows subprocess support")

    monkeypatch.setattr(js_discover, "async_playwright", unexpected_playwright)

    async def exercise():
        url = f"{BASE}/tenders"
        fetcher = FakeFetcher({url: '<html><body><script src="/bundle.js"></script></body></html>'})
        try:
            with pytest.raises(AttachFailed, match="run Uvicorn without --reload"):
                await learn_recipe(url, fetcher.for_portal("fixture"), None)
        finally:
            await fetcher.aclose()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(exercise())


def test_compressed_post_bodies_do_not_stop_discovery(monkeypatch):
    # TED's pages POST gzip bodies. Reading them as text crashed the whole browser discovery,
    # both for ignored POSTs and for ones that look like a replayable search.
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)

    async def exercise():
        await require_chromium()
        fetcher = FixtureFetcher({
            f"{BASE}/tenders": """<script>
                const body = new Uint8Array([31, 139, 8, 0, 0, 0, 0, 0, 0, 3]);
                const headers = {"Content-Type": "application/json"};
                Promise.allSettled([
                    fetch('/api/events', {method: 'POST', headers, body}),
                    fetch('/api/search', {method: 'POST', headers, body}),
                ]).then(() => fetch('/api/tenders'));
            </script>""",
            f"{BASE}/api/tenders": json.dumps({"items": [{"id": "SPA-1", "title": "Library repairs"}]}),
        })
        try:
            found = await js_discover._capture_json(f"{BASE}/tenders", fetcher.for_portal("fixture"))
            assert [key for key, _ in found] == [f"{BASE}/api/tenders"]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_discover_and_replay_public_json_feed_after_config_responses(monkeypatch):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    config_paths = ["/api/tenders/config", *[f"/api/config/{index}" for index in range(1, 17)]]
    first_feed = {
        "items": [{"id": "SPA-1", "title": "Library repairs", "url": "/notices/1"}],
        "next": "/api/tenders?page=2",
    }
    second_feed = {
        "items": [{"id": "SPA-2", "title": "School grounds", "url": "/notices/2"}],
        "next": None,
    }

    class FixtureLLM:
        model_name = "fixture-mini"

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            assert evidence["endpoints"][0]["index"] == 0
            assert evidence["endpoints"][0]["path"] == "/api/tenders"
            assert len(evidence["endpoints"]) <= 4
            assert "Library repairs" in evidence["endpoints"][0]["sample"]
            return {
                "endpoint": 0,
                "recipe": {
                    "list": {
                        "url": "https://unobserved.example/api", "item": "$.items[*]",
                        "next_page": "$.next", "max_pages": 25,
                    },
                    "fields": {"id": "$.id", "title": "$.title", "url": "$.url"},
                },
            }

    async def exercise():
        await require_chromium()
        fetcher = FixtureFetcher({
            f"{BASE}/tenders": """
                <link rel="stylesheet" href="/style.css">
                <script src="/bundle.js"></script>
            """,
            f"{BASE}/bundle.js": " " * 600_000 + f"""
                    Promise.all({json.dumps(config_paths)}.map(path => fetch(path)))
                        .then(() => setTimeout(() => fetch('/api/tenders'), 200));
                    fetch('/api/write', {{method: 'POST'}});
                    fetch('https://unobserved.example/api');
            """,
            **{
                f"{BASE}{path}": json.dumps({"items": [{"id": str(index), "title": "Theme options"}]})
                for index, path in enumerate(config_paths)
            },
            f"{BASE}/api/tenders": json.dumps(first_feed),
            f"{BASE}/api/tenders?page=2": json.dumps(second_feed),
        })
        try:
            scoped = fetcher.for_portal("fixture")
            recipe = await discover_json_api(f"{BASE}/tenders", scoped, FixtureLLM())
            assert recipe is not None
            assert recipe.list_.url == f"{BASE}/api/tenders"
            assert recipe.list_.max_pages == 25
            requested = [url for _, _, url in fetcher.request_log]
            assert requested[0] == f"{BASE}/tenders"
            assert requested[-1] == f"{BASE}/api/tenders"
            assert set(requested[1:-1]) == {f"{BASE}{path}" for path in ["/bundle.js", *config_paths]}
            tenders = await run_recipe(recipe, scoped)
            assert [item.fields["id"] for item in tenders] == ["SPA-1", "SPA-2"]
            assert tenders[1].url == f"{BASE}/notices/2"
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(("redirected", "subdomain"), [(False, False), (True, False), (False, True)])
def test_repair_rediscovers_moved_json_feed_from_original_entry(monkeypatch, redirected, subdomain):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    base = "https://www.portal.example" if redirected else ("https://portal.example" if subdomain else BASE)
    feed = "https://api.portal.example" if subdomain else base
    old_url = f"{feed}/api/tenders-old"
    entry_url = f"{base}/tenders"
    original_url = f"{BASE}/tenders" if redirected else entry_url
    new_url = f"{feed}/api/tenders"
    previous = Recipe.model_validate({
        "kind": "json_api", "list": {"url": old_url, "item": "$.items[*]"},
        "fields": {"id": "$.id", "title": "$.title"},
    })

    class FixtureLLM:
        strong_model_name = "fixture-repair"

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            assert strong
            assert old_url in evidence["previous_recipe"]
            assert evidence["endpoints"][0]["path"] == "/api/tenders"
            assert evidence["health_problems"] == ["portal returned HTTP 404"]
            return {
                "endpoint": 0,
                "recipe": {"list": {"item": "$.items[*]"}, "fields": {"id": "$.id", "title": "$.title"}},
            }

    async def exercise():
        await require_chromium()

        def respond(request):
            url = str(request.url)
            if redirected and url == original_url:
                return httpx.Response(301, headers={"location": entry_url})
            if url == entry_url:
                return httpx.Response(
                    200, headers={"content-type": "text/html"}, text=f"<script>fetch('{new_url}')</script>",
                )
            assert url == new_url
            return httpx.Response(200, headers={"Access-Control-Allow-Origin": base},
                                  json={"items": [{"id": "NEW-1", "title": "Roof replacement"}]})

        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            scoped = fetcher.for_portal("fixture")
            health = Health(ok=False, problems=["portal returned HTTP 404"])
            candidate = await repair(previous, health, scoped, FixtureLLM(), portal_url=original_url)
            assert candidate is not None
            assert candidate.list_.url == new_url
            prefix = [original_url, entry_url] if redirected else []
            assert [url for _, _, url in fetcher.request_log] == prefix + [entry_url, new_url, new_url]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("via_browser", [False, True])
def test_repair_can_replace_html_with_a_public_json_recipe(monkeypatch, via_browser):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    url = f"{BASE}/tenders"
    feed_url = f"{BASE}/api/tenders" if via_browser else url
    payload = json.dumps({"items": [{"id": "REF-1", "title": "Library repairs"}]})
    old = Recipe.model_validate({"list": {"url": url, "item": "//article"}})

    class Model:
        strong_model_name = "fixture-repair"

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            assert strong and "Library repairs" in evidence["endpoints"][0]["sample"]
            return {"endpoint": 0, "recipe": {
                "list": {"item": "$.items[*]"}, "fields": {"id": "$.id", "title": "$.title"},
            }}

    async def exercise():
        pages = {feed_url: payload}
        if via_browser:
            await require_chromium()
            pages[url] = "<html><body><script>fetch('/api/tenders')</script></body></html>"
        fetcher = FixtureFetcher(pages)
        try:
            scoped = fetcher.for_portal("fixture")
            candidate = await repair(old, Health(ok=False, problems=["no list entries"]), scoped, Model())
            assert candidate is not None and candidate.kind == "json_api"
            assert candidate.list_.url == feed_url
            assert [row.fields["id"] for row in await run_recipe(candidate, scoped)] == ["REF-1"]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
