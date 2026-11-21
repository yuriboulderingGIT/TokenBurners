"""Replay observed search traffic through the reader without storing the anonymous session."""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.fetcher import Fetcher
from app.models import Recipe, SearchTransport
from brain import js_discover
from brain.public_search import ObservedSearch, PublicVisit, SearchPages
from brain.run_recipe import PaginationError, RecipeReadError, run_recipe

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

    async def capture(url, fetch, *, target=None, llm=None):
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


GRAPHQL = "https://notices.example/graphql"


def observe_graphql(params):
    request = SimpleNamespace(url=GRAPHQL, headers={"content-type": "application/json"},
                              post_data=json.dumps(params))
    return ObservedSearch.observe(SOURCE, request, None, 0)


@pytest.mark.parametrize("body", [
    {"operationName": "Save", "query": "mutation Save($id: ID!) { save(id: $id) { id } }"},
    {"operationName": "Save", "query": "# document\nquery Read { reads } mutation Save { save }"},
    {"operationName": "Listen", "query": "subscription Listen { changes }"},
    {"operationName": "Unknown", "extensions": {"persistedQuery": {"sha256Hash": "abc"}}},
    {"query": '{ notices(nonce: "temporary") { id } }'},
    {"query": 'query List($nonce: String = "temporary"){ notices(nonce: $nonce) { id } }'},
])
def test_graphql_requires_a_read_operation(body):
    assert observe_graphql(body) is None


def test_search_identity_includes_public_selection_and_ignores_transient_state():
    query = "query Find($args: Input) { rows(args: $args) { id title } }"
    def observe(buyer, nonce, offset, text=query):
        return observe_graphql({"query": text, "operationName": "Find", "variables": {
            "args": {"filters": {"buyer": buyer, "nonce": nonce}, "sort": {"field": "title"}, "offset": offset},
        }})

    first = observe("A", "discard-first", 0)
    fresh = observe("A", "discard-second", 2, "# new comment\n" + query.replace(" {", "\n{"))
    other = observe("B", "discard-third", 0)
    assert first.spec.operation_id == fresh.spec.operation_id != other.spec.operation_id
    assert first.spec.selection_id == fresh.spec.selection_id != other.spec.selection_id
    assert all(value not in first.spec.model_dump_json() for value in ("discard-first", "nonce", query))
    def with_session_variable(nonce):
        return observe_graphql({"query": "query Fetch($context: String) { notices(csrfToken: $context) { id } }",
                                "variables": {"context": nonce}})
    assert with_session_variable("first").spec.operation_id == with_session_variable("second").spec.operation_id


def test_graphql_scope_includes_inline_filters_and_resolved_variable_values():
    def scope(query, **variables):
        return observe_graphql({"query": query, "variables": variables}).spec.selection_id

    base = "{ notices(filters: {status: OPEN, region: [EU]}) { id } }"
    assert scope(base) and scope(base) == scope(base.replace("{ id }", "{ id title }"))
    assert scope(base) != scope(base.replace("OPEN", "CLOSED"))
    by_variable = "query Read($choice: String) { notices(filters: { country: $choice }) { id } }"
    assert scope(by_variable, choice="BE") != scope(by_variable, choice="UK")
    with_default = 'query Read($choice: String = "Berlin") { notices(region: $choice) { id } }'
    assert scope(with_default) != scope(with_default.replace("Berlin", "Paris"))
    # Sort order and status defaults may change with a redesign without selecting other notices.
    by_status = "query Read($choice: Status = OPEN) { notices(status: $choice, sort: NEWEST) { id } }"
    assert scope(by_status) is None and scope(by_status.replace("NEWEST", "OLDEST")) is None


def test_legacy_search_must_be_unambiguous_and_saved_projection_must_match(monkeypatch):
    categories = observe_graphql({"query": "{ categories { id title } }"})
    notices = observe_graphql({"query": "{ notices { reference title } }"})
    wrong = {"records": [{"id": "C-1", "title": "Category"}]}
    desired = {"records": [{"reference": "N-1", "title": "Roof repair"}]}
    captured = [(categories, wrong), (notices, desired)]

    async def capture(*args, **kwargs):
        return captured

    monkeypatch.setattr(js_discover, "_capture_json", capture)
    async def exercise():
        old = SearchTransport(endpoint=GRAPHQL, media_type="application/json")
        with pytest.raises(RecipeReadError, match="multiple anonymous searches"):
            await SearchPages.open(SOURCE, old, None)
        saved = notices.spec.model_copy(update={"result_item": "$.records[*]",
                                                "record_fields": {"id": "$.reference", "title": "$.title"}})
        assert (await SearchPages.open(SOURCE, saved, None)).first_payload == desired
        captured[:] = [(notices, wrong)]
        with pytest.raises(RecipeReadError, match="could not reproduce"):
            await SearchPages.open(SOURCE, saved, None)
        assert (await SearchPages.open(SOURCE, old, None)).first_payload == wrong

    asyncio.run(exercise())


def test_browser_waits_for_delayed_named_search_and_rehydrates_its_session(monkeypatch):
    from tests.test_js_discover import require_chromium

    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 3)
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.02)
    visits = []
    posts = []

    def respond(request):
        if request.method == "GET":
            token = f"ephemeral-{len(visits) + 1}"
            visits.append(token)
            script = """
                const nonce = TOKEN;
                document.cookie = 'session=' + nonce + '; path=/';
                const search = (operationName, query) => fetch('/graphql', {method: 'POST',
                    headers: {'content-type': 'application/json', 'x-csrf-token':nonce},
                    body: JSON.stringify({operationName, query, variables: {nonce}})});
                search('Categories', 'query Categories { categories { id title } }')
                    .then(() => setTimeout(() =>
                        search('Notices', 'query Notices { notices { id title } }'), 300));
            """
            return httpx.Response(200, text="<script>" + script.replace("TOKEN", json.dumps(token)) + "</script>",
                                  headers={"content-type": "text/html"})
        body = json.loads(request.content)
        assert request.url == GRAPHQL
        token = body["variables"]["nonce"]
        assert request.headers["x-csrf-token"] == token and "session=" + token in request.headers["cookie"]
        posts.append((token, body["operationName"]))
        key = body["operationName"].lower()
        return httpx.Response(200, json={"data": {key: [{"id": key + "-1", "title": "Roof repair"}]}, "total": 1})

    class Model:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            endpoints = json.loads(prompt)["endpoints"]
            choice = next(item["index"] for item in endpoints if '"notices":' in item["sample"])
            return {"endpoint": choice, "recipe": {"list": {"item": "$.data.notices[*]", "total_path": ["total"]},
                                                   "fields": {"id": "$.id", "title": "$.title"}}}

    async def exercise():
        await require_chromium()
        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            fetch = fetcher.for_portal("fixture")
            learned = await js_discover.discover_json_api(SOURCE, fetch, Model())
            saved = learned.to_json()
            assert learned.list_.search.operation_id and learned.list_.search.result_item == "$.data.notices[*]"
            assert "ephemeral-" not in saved and "nonce" not in saved
            rows = await run_recipe(Recipe.model_validate_json(saved), fetch)
            assert [row.fields["id"] for row in rows] == ["notices-1"]
            assert posts == [(token, op) for token in visits for op in ("Categories", "Notices")]
            assert len(visits) == 2
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
