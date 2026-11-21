"""Source semantics and unique coverage for both candidate validation and full reads."""

import asyncio
import json
from pathlib import Path

import pytest

from app.fetcher import FakeFetcher
from app.models import Recipe
from brain.learn import validate_candidate
from brain.run_recipe import NotANotice, PaginationError, run_recipe


BASE = "https://portal.example"
LIST = BASE + "/results"
READERS = (validate_candidate, run_recipe)


def read(reader, recipe, pages):
    async def exercise():
        fetcher = FakeFetcher(pages)
        try:
            return await reader(Recipe.model_validate(recipe), fetcher.for_portal("test"))
        finally:
            await fetcher.aclose()
    return asyncio.run(exercise())


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize(("context", "href", "title"), [
    ('<main><h1>CPV categories</h1>{}</main>', '/browse/categoryOverview.do?code={}', "Construction"),
    ('<main><h1>Contracting authorities</h1>{}</main>', '/buyers/{}', "City council"),
    ('<section aria-label="News">{}</section>', '/news/{}', "City approves bridge plans"),
    ('<nav aria-label="Procurement">{}</nav>', '/tenders/{}', "Browse open tenders"),
    ('<main>{}</main>', '/tenders', "View all tenders"),
    ('<main>{}</main>', '/notices?cpv={}', "Construction works"),
    ('<main>{}</main>', '/entry/{}', "Unidentified card"),
])
def test_non_notice_cards_cannot_verify(reader, context, href, title):
    # The two failed German portals published 44 categories with distinct keys.
    cards = "".join(f'<article data-id="{i}"><a href="{href.format(i)}">{title} {i}</a></article>'
                    for i in range(44))
    recipe = {"list": {"url": LIST, "item": "//article"}, "fields": {"id": "@data-id", "title": "./a"}}
    with pytest.raises(NotANotice, match="verified procurement notice"):
        read(reader, recipe, {LIST: context.format(cards)})


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("key", ["categories", "buyers", "news", "menu"])
def test_json_facet_cannot_be_selected_even_by_wildcard(reader, key):
    recipe = {"kind": "json_api", "list": {"url": BASE + "/tenders", "item": "$.data.*[*]"},
              "fields": {"id": "$.id", "title": "$.title"}}
    payload = {"data": {key: [{"id": "117", "title": "Contract 117"}]}}
    with pytest.raises(NotANotice):
        read(reader, recipe, {BASE + "/tenders": json.dumps(payload)})


@pytest.mark.parametrize("reader", READERS)
def test_unknown_feed_with_no_notice_evidence_cannot_verify(reader):
    recipe = {"kind": "json_api", "list": {"url": LIST, "item": "$.items[*]"},
              "fields": {"id": "$.id", "title": "$.title"}}
    with pytest.raises(NotANotice, match="no corroborating evidence"):
        read(reader, recipe, {LIST: json.dumps({"items": [{"id": "1", "title": "Bridge"}]})})


@pytest.mark.parametrize("reader", READERS)
def test_sparse_notice_collection_and_buyer_filter_are_valid(reader):
    url = BASE + "/buyers/13/notices?cpv=45000000"
    recipe = {"kind": "json_api", "list": {"url": url, "item": "$.items[*]"},
              "fields": {"id": "$.id", "title": "$.title"}}
    result = read(reader, recipe, {url: json.dumps({"items": [{"id": "R-9", "title": "Roof"}]})})
    if reader is run_recipe:
        assert [row.fields["id"] for row in result] == ["R-9"]


def test_constant_fields_and_unrelated_links_cannot_corroborate_a_generic_card():
    recipe = {"list": {"url": LIST, "item": "//article"},
              "fields": {"id": "@data-id", "title": "./a", "buyerName": "'City council'", "url": "'/notice/99'"},
              "documents": {"link": "./a/@href"}}
    with pytest.raises(NotANotice):
        read(run_recipe, recipe, {LIST: '<article data-id="1"><a href="/entry/1">Painting</a></article>'})


@pytest.mark.parametrize("portal", ["vergabemarktplatz-nrw-de", "vergabe-brandenburg-de"])
def test_real_notice_tables_from_the_two_directory_portals_can_verify(portal):
    body = (Path(__file__).parents[1] / "testset" / "fixtures" / portal / "list.html").read_text()
    recipe = {"list": {"url": LIST, "item": "//tbody/tr[td[6]/a]"},
              "fields": {"id": "./td[6]/a/@href", "title": "./td[3]"}}
    rows = read(run_recipe, recipe, {LIST: body})
    assert rows and all(row.fields["title"] for row in rows)


@pytest.mark.parametrize("reader", READERS)
def test_overlap_rejects_twenty_rows_with_only_nineteen_identities(reader):
    url = LIST + "?offset=0"
    recipe = {"kind": "json_api", "list": {"url": url, "item": "$.notices[*]"},
              "fields": {"id": "$.id", "title": "$.title"}}
    def page(numbers):
        return json.dumps({"notices": [{"id": i, "title": "Roof repairs"} for i in numbers], "total": 20})
    with pytest.raises(PaginationError, match="20 tenders.*19 distinct identities in 20 entries"):
        read(reader, recipe, {url: page(range(1, 11)), LIST + "?offset=10": page(range(10, 20))})


def test_overlap_can_reconcile_with_a_real_third_page():
    recipe = {"kind": "json_api", "list": {"url": LIST, "item": "$.notices[*]", "next_page": "$.next"},
              "fields": {"id": "$.id", "title": "$.title"}}
    def page(numbers, following=None):
        return json.dumps({"notices": [{"id": i, "title": "Roof repairs"} for i in numbers],
                           "total": 4, "next": following})
    pages = {LIST: page([1, 2], "?p=2"), LIST + "?p=2": page([2, 3], "?p=3"),
             LIST + "?p=3": page([4])}
    assert {r.fields["id"] for r in read(run_recipe, recipe, pages)} == {"1", "2", "3", "4"}


def test_validation_checks_late_detail_links_without_fetching_them():
    recipe = {"list": {"url": LIST, "item": "//li"}, "detail": {"link": "./a/@href"},
              "fields": {"id": "//code", "title": "//h1"}}
    total = "<script>var totalCount=5; if (offset < totalCount) { load(); }</script>"
    pages = {LIST: total + "".join(f'<li><a href="/notice/{i}">Roof {i}</a></li>' for i in [1, 2, 3, 4, 4])}
    pages.update({BASE + f"/notice/{i}": f"<h1>Roof {i}</h1><code>{i}</code>" for i in (1, 2, 3)})
    with pytest.raises(PaginationError, match="4 distinct identities in 5 entries"):
        read(validate_candidate, recipe, pages)


def test_a_directory_link_among_notices_is_left_out():
    recipe = {"list": {"url": LIST, "item": "//li"}, "detail": {"link": "./a/@href"},
              "fields": {"id": "//code", "title": "//h1"}}
    pages = {LIST: "".join(f'<li><a href="/notice/{i}">Roof {i}</a></li>' for i in range(3))
             + '<li><a href="/categoryOverview.do?code=16">Other categories</a></li>'}
    pages.update({BASE + f"/notice/{i}": f"<h1>Roof {i}</h1><code>{i}</code>" for i in range(3)})
    read(validate_candidate, recipe, pages)
    assert [row.fields["id"] for row in read(run_recipe, recipe, pages)] == ["0", "1", "2"]


def test_mostly_directory_links_cannot_attach_or_read():
    recipe = {"list": {"url": LIST, "item": "//li"}, "detail": {"link": "./a/@href"},
              "fields": {"id": "//code", "title": "//h1"}}
    pages = {LIST: '<li><a href="/notice/0">Roof 0</a></li>' + "".join(
        f'<li><a href="/categoryOverview.do?code={i}">Category {i}</a></li>' for i in range(3))}
    pages[BASE + "/notice/0"] = "<h1>Roof 0</h1><code>0</code>"
    for reader in (validate_candidate, run_recipe):
        with pytest.raises(NotANotice, match="detail.link points to directory"):
            read(reader, recipe, pages)
