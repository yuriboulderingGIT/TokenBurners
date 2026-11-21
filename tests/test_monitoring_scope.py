import asyncio
import json

import pytest

from app.fetcher import FakeFetcher
from app.models import Recipe, SearchTransport
from brain.learn import AttachFailed, learn_recipe
from brain.public_search import selection_id
from brain.scope import monitoring_scope, preserves_scope, request_scope

BASE = "https://portal.example"
ROWS = ('<ul aria-label="Tender notices"><li data-id="N1"><h2>Road repairs</h2></li>'
        '<li data-id="N2"><h2>Library restoration</h2></li></ul>')


def recipe(path):
    return Recipe.model_validate({"list": {"url": BASE + path, "item": "//li"},
                                  "fields": {"id": "@data-id", "title": "./h2"}})


@pytest.mark.parametrize(("path", "expected"), [
    ("/search?latest=true", "Latest results window"),
    ("/notices/latest-10", "Latest 10 results"),
    ("/open-tenders", "Current open opportunities"),
    ("/results?publicationStatus=OFFERS", "Current open opportunities"),
    ("/notices?cpv=45000000&page=1", "Filtered results (cpv=45000000)"),
    ("/notices?where=very-private-text", "Filtered results (where)"),
    ("/notices?scope=archive", "Historical archive"),
    ("/notices/archive", "Historical archive"),
    ("/tenders?page=1&limit=10&sort=descending", "Source listing (historical coverage unverified)"),
])
def test_scope_comes_from_source_evidence(path, expected):
    assert monitoring_scope(BASE, recipe(path)) == expected


def test_repair_compares_repeated_query_values_and_actual_post_constraints():
    original = recipe("/search?region=x&region=y&page=1")
    assert preserves_scope(BASE, original, recipe("/renamed?region=y&region=x"))
    assert not preserves_scope(BASE, original, recipe("/search?region=y"))
    assert not preserves_scope(BASE, recipe("/buyers/a/notices"), recipe("/buyers/b/notices"))

    def search(buyer, secret):
        body = {"operationName": "Notices", "query": "query Notices { notices { id } }",
                "variables": {"filters": {"buyer": buyer, "csrfToken": secret}, "offset": 0}}
        return SearchTransport(endpoint=BASE + "/graphql", media_type="application/json",
                               scope=request_scope(body), selection_id=selection_id(body))

    old, new = recipe("/results"), recipe("/results")
    old.list_.search = search("supplier-one", "first-token")
    new.list_.search = search("supplier-one", "rotated-token")
    assert preserves_scope(BASE, old, new)
    new.list_.search = search("supplier-two", "rotated-token")
    assert not preserves_scope(BASE, old, new)
    assert monitoring_scope(BASE, new) == "Filtered results (buyer)"
    assert "supplier" not in new.to_json()
    assert "token" not in new.to_json()


class Model:
    model_name = strong_model_name = "fixture"

    def __init__(self, destination):
        self.destination = destination

    async def json(self, prompt, **kwargs):
        data = json.loads(prompt)
        if "entry" in data:
            return {"list_url": self.destination}
        listing = data["list_page"]["url"]
        candidate = recipe("/").model_dump(by_alias=True)
        candidate["list"].update(url=listing, scope_label="Historical archive")
        return candidate


@pytest.mark.parametrize(("start", "target", "entry_html"), [
    ("/tenders", "/tenders?buyer=123", 'Select buyer'),
    ("/tenders?buyer=111", "/tenders?buyer=222", 'Change buyer'),
    ("/tenders?status=open", "/tenders", 'View all tenders'),
    ("/tenders", "/tenders/latest", 'Latest notices'),
])
def test_attach_stays_on_the_requested_notices(start, target, entry_html):
    # The requested page already lists notices: attach reads it rather than switch to other ones.
    async def exercise():
        entry, other = BASE + start, BASE + target
        pages = FakeFetcher({entry: ROWS + f'<a href="{other}">{entry_html}</a>', other: ROWS})
        try:
            result = await learn_recipe(entry, pages.for_portal("p"), Model(other))
            assert result.list_.url == entry
            assert other not in [url for _, _, url in pages.request_log]
        finally:
            await pages.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize(("start", "target", "label"), [
    ("/market", "/search?country=DE", "View all tenders"),
    ("/home", "/tenders?status=open", "Open tenders"),
    ("/home", "/notices", "All notices"),
])
def test_attach_follows_the_portals_link_to_the_complete_or_open_listing(start, target, label):
    async def exercise():
        entry, listing = BASE + start, BASE + target
        pages = FakeFetcher({entry: f'<p>Welcome</p><a href="{listing}">{label}</a>', listing: ROWS})
        try:
            result = await learn_recipe(entry, pages.for_portal("p"), Model(listing))
            assert result.list_.url == listing
        finally:
            await pages.aclose()
    asyncio.run(exercise())


def test_attach_refuses_a_buyer_filter_when_the_entry_lists_nothing():
    async def exercise():
        entry, other = BASE + "/home", BASE + "/tenders?buyer=123"
        pages = FakeFetcher({entry: f'<p>Welcome</p><a href="{other}">Select buyer</a>', other: ROWS})
        try:
            with pytest.raises(AttachFailed, match="monitoring scope"):
                await learn_recipe(entry, pages.for_portal("p"), Model(other))
        finally:
            await pages.aclose()
    asyncio.run(exercise())


def test_listing_heading_overrides_model_scope_claim_and_survives_reload():
    async def exercise():
        entry = BASE + "/procurement"
        pages = FakeFetcher({entry: "<main><h1>Latest 10 calls for tenders</h1>" + ROWS + "</main>"})
        try:
            result = await learn_recipe(entry, pages.for_portal("p"), Model(entry))
            assert monitoring_scope(entry, Recipe.model_validate_json(result.to_json())) == "Latest 10 results"
        finally:
            await pages.aclose()
    asyncio.run(exercise())
