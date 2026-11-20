"""Feeds that real portals load through CDN widgets, offset query strings or GraphQL."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.fetcher import FakeFetcher, Fetcher
from app.models import Recipe
from brain import js_discover
from brain.public_search import ObservedSearch, PublicVisit
from brain.run_recipe import PaginationError, run_recipe
from tests.test_js_discover import FixtureFetcher, require_chromium

PORTAL = "https://portal.example"
FEED = PORTAL + "/ws/tender/fe?$limit=2&$offset=0&query=%7B%22type%22:1%7D"


def _feed(offset: int, ids: list[str], total: int = 5) -> str:
    return json.dumps({"data": [{"id": i, "title": f"Gara {i}"} for i in ids],
                       "meta": {"nb_total": total, "limit": "2", "offset": str(offset)}})


def _recipe() -> Recipe:
    return Recipe.model_validate({"kind": "json_api", "list": {"url": FEED, "item": "$.data[*]", "max_pages": 100},
                                  "fields": {"id": "$.id", "title": "$.title"}})


def _read(pages: dict[str, str]):
    async def go():
        fetch = FakeFetcher(pages)
        try:
            return await run_recipe(_recipe(), fetch.for_portal("p"))
        finally:
            await fetch.aclose()
    return asyncio.run(go())


def test_feed_without_next_link_advances_its_offset_until_the_stated_total():
    page = lambda n: FEED.replace("$offset=0", f"$offset={n}")  # noqa: E731
    rows = _read({FEED: _feed(0, ["1", "2"]), page(2): _feed(2, ["3", "4"]), page(4): _feed(4, ["5"])})
    assert [row.fields["id"] for row in rows] == ["1", "2", "3", "4", "5"]


def test_feed_that_ignores_its_offset_is_not_served_as_complete():
    with pytest.raises(PaginationError, match="repeated"):
        _read({FEED: _feed(0, ["1", "2"]), FEED.replace("$offset=0", "$offset=2"): _feed(0, ["1", "2"])})


def test_graphql_query_replays_its_nested_page_variable(monkeypatch):
    endpoint = PORTAL + "/graphql"
    first = {"operationName": "procurements", "query": "query procurements { procurements { content { id } } }",
             "variables": {"pagination": {"page": 0, "pageSize": 2}, "filters": {"competition": False}}}
    pages = []

    def respond(request):
        body = json.loads(request.content)
        assert body["query"] == first["query"]
        page = body["variables"]["pagination"]["page"]
        pages.append(page)
        ids = [["95", "94"], ["91"]][page]
        return httpx.Response(200, json={"data": {"procurements": {
            "pageInfo": {"totalElements": 3}, "content": [{"id": i, "name": f"Zamówienie {i}"} for i in ids]}}})

    async def capture(url, fetch, **kwargs):
        request = SimpleNamespace(url=endpoint, headers={"content-type": "application/json"},
                                  post_data=json.dumps(first))
        observed = ObservedSearch.observe(url, request, PublicVisit(fetch), 0)
        assert observed.spec.page_path == ["variables", "pagination"]
        return [(observed, await observed.read(0, 0))]

    monkeypatch.setattr(js_discover, "_capture_json", capture)

    class Model:
        model_name = "fixture"

        async def json(self, *args, **kwargs):
            return {"endpoint": 0, "recipe": {
                "list": {"item": "$.data.procurements.content[*]",
                         "total_path": ["data", "procurements", "pageInfo", "totalElements"]},
                "fields": {"id": "$.id", "title": "$.name"}}}

    async def exercise():
        client = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            fetch = client.for_portal("pl")
            recipe = await js_discover.discover_json_api(PORTAL + "/procurements", fetch, Model())
            rows = await run_recipe(Recipe.model_validate_json(recipe.to_json()), fetch)
            assert [row.fields["id"] for row in rows] == ["95", "94", "91"]
            assert pages[-2:] == [0, 1]
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_graphql_mutation_is_never_replayed():
    request = SimpleNamespace(url=PORTAL + "/graphql", headers={"content-type": "application/json"},
                              post_data=json.dumps({"query": "mutation { save(page: 1) }"}))
    assert ObservedSearch.observe(PORTAL + "/procurements", request, None, 0) is None


def test_listing_widget_from_a_cdn_reveals_the_portal_feed(monkeypatch):
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.05)
    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    base = "http://portal.example"

    async def exercise():
        await require_chromium()
        fetcher = FixtureFetcher({
            f"{base}/list": '<list-widget></list-widget><script src="https://cdn.example/widget.js"></script>',
            # Like the real widget, it loads a large settings file before its list.
            "https://cdn.example/widget.js": "fetch('https://cdn.example/api/x'); fetch('/api/translations')"
                                             ".then(r => r.json()).then(() => fetch('/api/tenders?offset=0'));",
            f"{base}/api/translations": json.dumps({"sha": "1", "translations": "x" * 600_000}),
            f"{base}/api/tenders?offset=0": json.dumps({"tenders": [{"id": "1", "title": "Gara"}]}),
        })
        try:
            responses = await js_discover._capture_json(f"{base}/list", fetcher.for_portal("it"))
            assert [endpoint for endpoint, _ in responses] == [f"{base}/api/tenders?offset=0"]
            assert "https://cdn.example/api/x" not in [url for _, _, url in fetcher.request_log]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
