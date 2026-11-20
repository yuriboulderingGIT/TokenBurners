"""Replay observed search traffic through the reader without storing the anonymous session."""

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.fetcher import Fetcher
from app.models import Recipe
from brain import js_discover
from brain.public_search import ObservedSearch, PublicVisit
from brain.run_recipe import PaginationError, run_recipe

SOURCE = "https://notices.example/opportunites"
ENDPOINT = "https://notices.example/api/search"


def test_search_replays_html_offsets_without_saving_request_body(monkeypatch):
    offsets = []

    def respond(request):
        assert request.method == "POST"
        params = parse_qs(request.content.decode())
        assert params["nonce"] == ["ephemeral"]
        offset = int(params["offset"][0])
        offsets.append(offset)
        return httpx.Response(200, json={"rows": f'<article data-id="{offset}"><b>Marché {offset}</b>'
                              f'<a href="/notice/{offset}">Ouvrir</a><a href="/buyer">Acheteur</a>'
                              '<a href="/spec.pdf">Dossier</a></article>'})

    async def capture(url, fetch, *, target=None):
        assert url == SOURCE
        request = SimpleNamespace(url=ENDPOINT, headers={"content-type": "application/x-www-form-urlencoded"},
                                  post_data="offset=1&nonce=ephemeral")
        transport = ObservedSearch.observe(url, request, PublicVisit(fetch), 2)
        return [(transport, await transport.read(0, 0))]

    monkeypatch.setattr(js_discover, "_capture_json", capture)

    class Model:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            assert "ephemeral" not in prompt
            return {"endpoint": 0, "recipe": {"list": {"item": "//article"},
                                              "fields": {"id": "@data-id", "title": "./b", "buyerName": None},
                                              "documents": {"link": ".//a/@href"}}}

    async def exercise():
        client = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            fetch = client.for_portal("one")
            recipe = await js_discover.discover_json_api(SOURCE, fetch, Model())
            saved = recipe.to_json()
            assert "ephemeral" not in saved and "nonce" not in saved
            rows = await run_recipe(Recipe.model_validate_json(saved), fetch)
            assert [row.fields["id"] for row in rows] == ["0", "1"]
            assert rows[0].url == "https://notices.example/notice/0"
            assert rows[0].document_links == ["https://notices.example/spec.pdf"]
            assert offsets[-2:] == [0, 1]
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_compressed_search_body_is_ignored_instead_of_crashing():
    # Playwright decodes post_data as UTF-8 and raises on a gzip body, which TED's pages send.
    class CompressedPost:
        url = ENDPOINT
        headers = {"content-type": "application/json"}

        @property
        def post_data(self):
            return bytes([0x1F, 0x8B, 0x08, 0x00]).decode()

    assert ObservedSearch.observe(SOURCE, CompressedPost(), None, 0) is None


def test_unpaged_public_search_rejects_partial_records(monkeypatch):
    async def capture(url, fetch, **kwargs):
        request = SimpleNamespace(url=ENDPOINT, headers={"content-type": "application/json"}, post_data="{}")
        observed = ObservedSearch.observe(url, request, PublicVisit(fetch), 0)
        return [(observed, await observed.read(0, 0))]
    monkeypatch.setattr(js_discover, "_capture_json", capture)
    class Model:
        model_name = "fixture"

        async def json(self, *args, **kwargs):
            return {"endpoint": 0, "recipe": {"list": {"item": "$.items[*]", "total_path": ["meta", "nombre"]},
                                              "fields": {"id": "$.id", "title": "$.title"}}}

    async def exercise():
        client = Fetcher(min_interval=0, transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"items": [{"id": "1", "title": "Pont"}], "meta": {"nombre": 17}})))
        try:
            recipe = await js_discover.discover_json_api(SOURCE, client.for_portal("test"), Model())
            with pytest.raises(PaginationError, match="17 tenders.*only 1"):
                await run_recipe(recipe, client.for_portal("test"))
        finally:
            await client.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize("url", ["https://other.example/search", "https://notices.example/save",
                                ENDPOINT + "?session=persisted"])
def test_unrelated_write_or_session_url_is_never_replayed(url):
    request = SimpleNamespace(url=url, headers={}, post_data="")
    assert ObservedSearch.observe(SOURCE, request, None, 0) is None
