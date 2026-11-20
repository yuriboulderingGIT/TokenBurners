"""Run a browser against an offline fixture, then replay its JSON feed."""

import asyncio
import json
import os

import pytest
from playwright.async_api import async_playwright

from app.fetcher import FakeFetcher
from app.models import Health, Recipe
from brain import js_discover
from brain.js_discover import discover_json_api
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
        return response


async def require_chromium():
    async with async_playwright() as playwright:
        if not os.path.isfile(playwright.chromium.executable_path):
            pytest.skip("Playwright Chromium is not installed")


def test_discover_and_replay_public_json_feed_after_config_responses(monkeypatch):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    config_paths = ["/api/tenders/config", *[f"/api/config/{index}" for index in range(1, 5)]]
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
            f"{BASE}/tenders": f"""
                <link rel="stylesheet" href="/style.css">
                <script>
                    Promise.all({json.dumps(config_paths)}.map(path => fetch(path)))
                        .then(() => setTimeout(() => fetch('/api/tenders'), 200));
                    fetch('/api/write', {{method: 'POST'}});
                    fetch('https://unobserved.example/api');
                </script>
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
            assert set(requested[1:-1]) == {f"{BASE}{path}" for path in config_paths}
            tenders = await run_recipe(recipe, scoped)
            assert [item.fields["id"] for item in tenders] == ["SPA-1", "SPA-2"]
            assert tenders[1].url == f"{BASE}/notices/2"
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_repair_rediscovers_moved_json_feed_from_original_entry(monkeypatch):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    old_url = f"{BASE}/api/tenders-old"
    entry_url = f"{BASE}/tenders"
    new_url = f"{BASE}/api/tenders"
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
        fetcher = FixtureFetcher({
            entry_url: "<script>fetch('/api/tenders');</script>",
            new_url: json.dumps({"items": [{"id": "NEW-1", "title": "Roof replacement"}]}),
        })
        try:
            scoped = fetcher.for_portal("fixture")
            health = Health(ok=False, problems=["portal returned HTTP 404"])
            candidate = await repair(previous, health, scoped, FixtureLLM(), portal_url=entry_url)
            assert candidate is not None
            assert candidate.list_.url == new_url
            assert [url for _, _, url in fetcher.request_log] == [entry_url, new_url, new_url]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
