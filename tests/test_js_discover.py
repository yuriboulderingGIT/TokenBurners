"""Run a browser against an offline fixture, then replay its JSON feed."""

import asyncio
import json
import os

import pytest
from playwright.async_api import async_playwright

from app.fetcher import FakeFetcher
from brain.js_discover import discover_json_api
from brain.run_recipe import run_recipe

BASE = "http://portal.example"


def test_discover_and_replay_public_json_feed():
    requested = []
    first_feed = {
        "items": [{"id": "SPA-1", "title": "Library repairs", "url": "/notices/1"}],
        "next": "/api/tenders?page=2",
    }
    second_feed = {
        "items": [{"id": "SPA-2", "title": "School grounds", "url": "/notices/2"}],
        "next": None,
    }

    class FixtureFetcher(FakeFetcher):
        async def get(self, portal_id, url, **kwargs):
            requested.append(url)
            response = await super().get(portal_id, url, **kwargs)
            response.headers["content-type"] = (
                "application/json" if "/api/" in url else "text/html; charset=utf-8"
            )
            return response

    class FixtureLLM:
        model_name = "fixture-mini"

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            assert [{k: e[k] for k in ("index", "path")} for e in evidence["endpoints"]] == [
                {"index": 0, "path": "/api/tenders"}
            ]
            assert "Library repairs" in evidence["endpoints"][0]["sample"]
            return {
                "endpoint": 0,
                "recipe": {
                    "list": {"url": "https://unobserved.example/api", "item": "$.items[*]", "next_page": "$.next"},
                    "fields": {"id": "$.id", "title": "$.title", "url": "$.url"},
                },
            }

    async def exercise():
        async with async_playwright() as playwright:
            if not os.path.isfile(playwright.chromium.executable_path):
                pytest.skip("Playwright Chromium is not installed")
        fetcher = FixtureFetcher({
            f"{BASE}/tenders": """
                <link rel="stylesheet" href="/style.css">
                <script>
                    fetch('/api/tenders');
                    fetch('/api/write', {method: 'POST'});
                    fetch('https://unobserved.example/api');
                </script>
            """,
            f"{BASE}/api/tenders": json.dumps(first_feed),
            f"{BASE}/api/tenders?page=2": json.dumps(second_feed),
        })
        try:
            scoped = fetcher.for_portal("fixture")
            recipe = await discover_json_api(f"{BASE}/tenders", scoped, FixtureLLM())
            assert recipe is not None
            assert recipe.list_.url == f"{BASE}/api/tenders"
            assert requested == [f"{BASE}/tenders", f"{BASE}/api/tenders"]
            tenders = await run_recipe(recipe, scoped)
            assert [item.fields["id"] for item in tenders] == ["SPA-1", "SPA-2"]
            assert tenders[1].url == f"{BASE}/notices/2"
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
