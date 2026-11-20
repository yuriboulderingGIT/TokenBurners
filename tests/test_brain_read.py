import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.fetcher import FakeFetcher, Fetcher
from app.models import Locale, RawTender, Recipe
from brain.blocks import Blocked
from brain.learn import validate_candidate
from brain.normalize import normalize
from brain.run_recipe import RecipeReadError, run_recipe
from mock_portal.server import CAPTCHA_PAGE, TENDERS, render_detail, render_list

_MOCK_ORIGIN = "http://localhost:8090"
_MOCK_RECIPE = Path(__file__).parents[1] / "testset/fixtures/mock/recipe_v1.json"


async def _read(
    recipe: Recipe, pages: dict[str, str | bytes], *, max_tenders: int | None = None,
    verify_all_pages: bool = False,
) -> list[RawTender]:
    fetcher = FakeFetcher(pages)
    try:
        return await run_recipe(
            recipe, fetcher.for_portal("test"),
            max_tenders=max_tenders, verify_all_pages=verify_all_pages,
        )
    finally:
        await fetcher.aclose()


def _mock_pages(ids: list[str], *, second_page: bool = False) -> dict[str, str]:
    pages = {f"{_MOCK_ORIGIN}/tenders": render_list(1)}
    if second_page:
        pages[f"{_MOCK_ORIGIN}/tenders?page=2"] = render_list(2)
    for tender_id in ids:
        body = render_detail(tender_id)
        assert body is not None
        pages[f"{_MOCK_ORIGIN}/tender/{tender_id}"] = body
    return pages


def test_html_recipe_reads_both_mock_pages() -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    ids = [tender["id"] for tender in TENDERS]
    output = asyncio.run(_read(recipe, _mock_pages(ids, second_page=True)))
    assert [tender.fields["id"] for tender in output] == ids
    assert output[0].document_links == [
        f"{_MOCK_ORIGIN}/download/MOCK-001/tender-documents.pdf"
    ]
    clean = normalize(output[0], recipe.locale)
    assert clean is not None
    assert clean.publicationDate == "2026-11-20"
    assert clean.submissionDeadline == "2026-12-09T10:00:00+00:00"
    assert clean.cpvCodes == ["45233140"]


def test_validation_sample_stops_before_the_next_request() -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    output = asyncio.run(_read(recipe, _mock_pages(["MOCK-001"]), max_tenders=1))
    assert [tender.fields["id"] for tender in output] == ["MOCK-001"]


def test_multiple_gone_details_fail_after_both_mock_pages_are_counted():
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    pages = _mock_pages(["MOCK-003", "MOCK-004", "MOCK-005"], second_page=True)
    with pytest.raises(RecipeReadError, match="2 of 5 tender detail pages"):
        asyncio.run(_read(recipe, pages))


def test_dead_detail_counts_towards_the_validation_request_limit():
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    pages = _mock_pages(["MOCK-002", "MOCK-003"], second_page=True)
    sample = asyncio.run(_read(recipe, pages, max_tenders=3, verify_all_pages=True))
    assert [row.fields["id"] for row in sample] == ["MOCK-002", "MOCK-003"]


@pytest.mark.parametrize("selector", [None, "//a[@class='old-pager']/@href"])
def test_missing_next_selector_rejected_even_with_a_full_sample(selector) -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    recipe.list_.next_page = selector
    pages = _mock_pages(["MOCK-001", "MOCK-002", "MOCK-003"])
    with pytest.raises(RecipeReadError, match="next page"):
        asyncio.run(_read(recipe, pages, max_tenders=3))


@pytest.mark.parametrize("broken", [False, True])
def test_candidate_checks_page_two_without_fetching_extra_details(broken) -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    pages = _mock_pages(["MOCK-001", "MOCK-002", "MOCK-003"], second_page=True)
    if broken:
        pages[f"{_MOCK_ORIGIN}/tenders?page=2"] = render_list(2, layout="v2")

    async def validate():
        fetcher = FakeFetcher(pages)
        try:
            await validate_candidate(recipe, fetcher.for_portal("test"))
        finally:
            await fetcher.aclose()

    if broken:
        with pytest.raises(RecipeReadError, match="page 2"):
            asyncio.run(validate())
    else:
        asyncio.run(validate())


def test_json_continuation_with_changed_items_is_incomplete() -> None:
    url = f"{_MOCK_ORIGIN}/feed"
    recipe = Recipe.model_validate({
        "kind": "json_api", "list": {"url": url, "item": "$.items[*]", "next_page": "$.next"},
        "fields": {"id": "$.id"},
    })
    pages = {url: '{"items":[{"id":"a"}],"next":"?p=2"}', url + "?p=2": '{"records":[{"id":"b"}]}'}
    with pytest.raises(RecipeReadError, match="page 2"):
        asyncio.run(_read(recipe, pages))


def test_json_recipe_must_follow_an_advertised_next_link() -> None:
    url = f"{_MOCK_ORIGIN}/feed"
    recipe = Recipe.model_validate({
        "kind": "json_api", "list": {"url": url, "item": "$.items[*]"},
        "fields": {"id": "$.id"},
    })
    pages = {url: '{"items":[{"id":"a"}],"_links":{"next":{"href":"?p=2"}}}'}
    with pytest.raises(RecipeReadError, match="missed the advertised next"):
        asyncio.run(_read(recipe, pages))


def test_head_next_link_and_disabled_final_page_control() -> None:
    url = f"{_MOCK_ORIGIN}/tenders"
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//li", "next_page": "//link[@rel='next']/@href"},
        "fields": {"id": "text()"},
    })
    pages = {
        url: '<html><head><link rel="next" href="?p=2"></head><body><ul><li>A</li></ul></body></html>',
        url + "?p=2": '<ul><li>B</li></ul><a aria-disabled="true" href="?p=3">Next page</a>',
    }
    assert [row.fields["id"] for row in asyncio.run(_read(recipe, pages))] == ["A", "B"]
    recipe.list_.max_pages = 1
    with pytest.raises(RecipeReadError, match="max_pages"):
        asyncio.run(_read(recipe, pages))


@pytest.mark.parametrize("next_relation", ["", ' rel="next"'])
def test_declared_last_page_ignores_only_a_nonsemantic_forward_control(next_relation):
    url = f"{_MOCK_ORIGIN}/tenders"
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//li", "next_page": "//a[@class='forward']/@href"},
        "fields": {"id": "text()"},
    })
    pages = {
        url: '<li>A</li><a rel="last" href="?start=10">2</a><a class="forward" href="?start=10">→</a>',
        url + "?start=10": (
            '<li>B</li><a rel="last" href="?start=10">2</a>'
            f'<a class="forward" href="?start=20"{next_relation}>→</a>'
        ),
        url + "?start=20": "<ol></ol>",
    }
    if next_relation:
        with pytest.raises(RecipeReadError, match="no tenders on page 3"):
            asyncio.run(_read(recipe, pages))
    else:
        assert [row.fields["id"] for row in asyncio.run(_read(recipe, pages))] == ["A", "B"]


@pytest.mark.parametrize("control", [
    '<a href="?page=2">Next</a>',
    '<a href="?seite=2">Weiter »</a>',
    '<a href="/tenders/page/2">Suivant</a>',
    '<nav class="paging"><a href="?step=2">Volgende</a></nav>',
    '<a href="?step=2" aria-label="Página siguiente">›</a>',
])
def test_common_next_labels_reveal_missing_pagination(control: str) -> None:
    url = f"{_MOCK_ORIGIN}/tenders"
    recipe = Recipe.model_validate({"list": {"url": url, "item": "//li"}})
    pages = {url: f"<ul><li>A</li></ul>{control}"}
    with pytest.raises(RecipeReadError, match="missed the advertised next"):
        asyncio.run(_read(recipe, pages))


def test_unrelated_next_links_and_disabled_controls_are_ignored() -> None:
    url = f"{_MOCK_ORIGIN}/tenders"
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//li[@data-id]"}, "fields": {"id": "@data-id"},
    })
    pages = {url: """
        <ul><li data-id="A">Tender A</li></ul>
        <nav><a href="/apply">Next</a></nav>
        <ul class="pagination"><li class="disabled"><a href="?page=2">Next</a></li></ul>
    """}
    assert [row.fields["id"] for row in asyncio.run(_read(recipe, pages))] == ["A"]


def test_etenders_next_button_follows_the_observed_href_without_executing_script():
    url = f"{_MOCK_ORIGIN}/search"
    button = '<div aria-label="pagination toolbar"><button title="Next" href="?d-3680175-p=2"></button></div>'
    pages = {url: "<li>A</li>" + button, url + "?d-3680175-p=2": "<li>B</li>"}
    recipe = Recipe.model_validate({"list": {"url": url, "item": "//li"}, "fields": {"id": "text()"}})
    with pytest.raises(RecipeReadError, match="missed the advertised next page"):
        asyncio.run(_read(recipe, pages))
    recipe.list_.next_page = "//button[@title='Next']/@href"
    assert [row.fields["id"] for row in asyncio.run(_read(recipe, pages))] == ["A", "B"]


def test_active_postback_pager_cannot_serve_only_its_first_page():
    url = f"{_MOCK_ORIGIN}/search"
    button = '<div class="pager"><button href="javascript:void(0)" onclick="postback()">Next</button></div>'
    recipe = Recipe.model_validate({"list": {"url": url, "item": "//li"}, "fields": {"id": "text()"}})
    with pytest.raises(RecipeReadError, match="next page control provides no public HTTP link"):
        asyncio.run(_read(recipe, {url: "<li>A</li>" + button}))
    last = button.replace("<button ", "<button disabled ")
    assert len(asyncio.run(_read(recipe, {url: "<li>A</li>" + last}))) == 1


def test_public_get_pager_replays_the_published_hidden_fields_and_next_button():
    url = f"{_MOCK_ORIGIN}/tenders"
    pager = """<form action="/results" method="get">
      <input type="hidden" name="state" value="some/value">
      <button type="submit" name="pageNum" value="1">Next page</button>
    </form>"""
    recipe = Recipe.model_validate({"list": {"url": url, "item": "//li"}, "fields": {"id": "text()"}})
    pages = {url: "<li>A</li>" + pager, f"{_MOCK_ORIGIN}/results?state=some%2Fvalue&pageNum=1": "<li>B</li>"}
    assert [row.fields["id"] for row in asyncio.run(_read(recipe, pages))] == ["A", "B"]
    for unsafe in (pager.replace('method="get"', 'method="post"'),
                   pager.replace('action="/results"', 'action="https://unrelated.example/results"'),
                   pager.replace('<button', '<button formaction="/other"')):
        with pytest.raises(RecipeReadError, match="next page control provides no public HTTP link"):
            asyncio.run(_read(recipe, {url: "<li>A</li>" + unsafe}))


@pytest.mark.parametrize("metadata", [{"total_count": 4}, {"pagination": {"totalCount": "4"}}])
def test_json_validation_counts_the_whole_collection(metadata: dict) -> None:
    url = f"{_MOCK_ORIGIN}/feed"
    recipe = Recipe.model_validate({
        "kind": "json_api", "list": {"url": url, "item": "$.items[*]", "next_page": "$.next"},
        "fields": {"id": "$.id"},
    })
    pages = {
        url: json.dumps({**metadata, "items": [{"id": "A"}, {"id": "B"}], "next": "?p=2"}),
        url + "?p=2": '{"items":[{"id":"C"}]}',
    }
    with pytest.raises(RecipeReadError, match="advertised 4 tenders.*only 3 entries"):
        asyncio.run(_read(recipe, pages, max_tenders=1, verify_all_pages=True))
    pages[url + "?p=2"] = '{"items":[{"id":"C"},{"id":"D"}]}'
    sample = asyncio.run(_read(recipe, pages, max_tenders=1, verify_all_pages=True))
    assert [row.fields["id"] for row in sample] == ["A"]


def test_boamp_fixture_cannot_serve_a_three_record_snapshot_as_the_whole_feed() -> None:
    snapshot = Path(__file__).parents[1] / "testset/fixtures/boamp-fr/list.json"
    url = f"{_MOCK_ORIGIN}/boamp/records?limit=3"
    recipe = Recipe.model_validate({
        "kind": "json_api", "list": {"url": url, "item": "$.results[*]"},
        "fields": {"id": "$.idweb", "title": "$.objet"},
    })

    async def check():
        fetcher = FakeFetcher({url: snapshot.read_bytes()})
        try:
            fetch = fetcher.for_portal("boamp")
            for read in (run_recipe, validate_candidate):
                with pytest.raises(RecipeReadError, match=r"1728870 tenders.*only 3 entries"):
                    await read(recipe, fetch)
        finally:
            await fetcher.aclose()

    asyncio.run(check())


def test_json_recipe_handles_array_paths_and_relative_urls() -> None:
    recipe = Recipe.model_validate({
        "kind": "json_api",
        "list": {"url": f"{_MOCK_ORIGIN}/api/list", "item": "$.items", "next_page": "$.next"},
        "fields": {"id": "$.id", "title": "$.title", "url": "$.publicUrl"},
        "documents": {"link": "$.documents[*].href"},
    })
    pages = {
        f"{_MOCK_ORIGIN}/api/list": json.dumps({
            "items": [{
                "id": 1,
                "title": "Road repairs",
                "publicUrl": "/tenders/1",
                "documents": [{"href": "../docs/1"}, {"href": "https://external.example/doc"}],
            }],
            "next": "?page=2",
        }),
        f"{_MOCK_ORIGIN}/api/list?page=2": json.dumps({
            "items": [{"id": 2, "title": "Public parks", "publicUrl": "https://notices.example/tenders/2"}],
            "next": None,
        }),
    }
    output = asyncio.run(_read(recipe, pages))
    assert [(item.fields["id"], item.url) for item in output] == [
        ("1", f"{_MOCK_ORIGIN}/tenders/1"),
        ("2", "https://notices.example/tenders/2"),
    ]
    assert output[0].document_links == [f"{_MOCK_ORIGIN}/docs/1", "https://external.example/doc"]


@pytest.mark.parametrize(("url", "error"), [
    ("https://external.example/tenders", "off-origin URL"),
    ("/tenders", "pagination revisited"),
])
def test_invalid_pagination_is_not_treated_as_a_complete_read(url: str, error: str) -> None:
    recipe = Recipe.model_validate({
        "list": {"url": f"{_MOCK_ORIGIN}/tenders", "item": "//li", "next_page": "//a/@href"},
        "fields": {"id": "./text()"},
    })
    pages = {f"{_MOCK_ORIGIN}/tenders": f'<ul><li>ID-1</li></ul><a href="{url}">Next</a>'}
    with pytest.raises(RecipeReadError, match=error):
        asyncio.run(_read(recipe, pages))


def test_cross_host_documents_must_be_public_links_observed_in_the_row():
    url = f"{_MOCK_ORIGIN}/tenders"
    pages = {url: '<li><a href="https://user@downloads.example/private">file</a></li>'}
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//li"},
        "documents": {"link": ".//a/@href"},
    })
    assert asyncio.run(_read(recipe, pages))[0].document_links == []
    recipe.documents.link = "'https://downloads.example/unobserved'"
    assert asyncio.run(_read(recipe, pages))[0].document_links == []


def test_pagination_does_not_follow_urls_invented_by_a_selector() -> None:
    recipe = Recipe.model_validate({
        "list": {"url": f"{_MOCK_ORIGIN}/tenders", "item": "//li", "next_page": "'/invented'"},
    })
    pages = {f"{_MOCK_ORIGIN}/tenders": '<ul><li>ID-1</li></ul><a href="/next">Next</a>'}
    with pytest.raises(RecipeReadError, match="absent from the page's links"):
        asyncio.run(_read(recipe, pages))


def test_reader_checks_redirect_before_fetcher_follows_it() -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    requests = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://external.example/tenders"})

    async def read() -> None:
        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(redirect))
        try:
            await run_recipe(recipe, fetcher.for_portal("test"))
        finally:
            await fetcher.aclose()

    with pytest.raises(RecipeReadError, match="redirected outside"):
        asyncio.run(read())
    assert requests == [recipe.list_.url]


def test_captcha_page_is_reported_as_blocked() -> None:
    recipe = Recipe.model_validate_json(_MOCK_RECIPE.read_text())
    with pytest.raises(Blocked, match="captcha"):
        asyncio.run(_read(recipe, {f"{_MOCK_ORIGIN}/tenders": CAPTCHA_PAGE}))


@pytest.mark.parametrize(("value", "locale", "expected"), [
    ("09.12.2026, 10:00 Uhr", Locale(lang="de", tz="Europe/Berlin"), "2026-12-09T10:00:00+01:00"),
    ("30.11.2026, 10:00h", Locale(lang="de", tz="Europe/Berlin"), "2026-11-30T10:00:00+01:00"),
    ("9 décembre 2026, 10h30", Locale(lang="fr", tz="Europe/Paris"), "2026-12-09T10:30:00+01:00"),
    ("9 de diciembre de 2026, 10:00", Locale(lang="es", tz="Europe/Madrid"), "2026-12-09T10:00:00+01:00"),
    ("12/09/2026, 10:00 AM", Locale(lang="en-US", tz="America/New_York"), "2026-12-09T10:00:00-05:00"),
    ("09/12/2026, 10:00 AM", Locale(lang="en-IE", tz="Europe/Dublin"), "2026-12-09T10:00:00+00:00"),
    ("2026-06-09T10:00:00", Locale(tz="Europe/Berlin"), "2026-06-09T10:00:00+02:00"),
    ("2026-12-09T10:00:00-03:00", Locale(), "2026-12-09T10:00:00-03:00"),
    ("2026-12-09T10:00:00Z", Locale(), "2026-12-09T10:00:00+00:00"),
    ("2026-12-09T10:00:00", Locale(), None),
    ("2026-12-09", Locale(tz="UTC"), None),
    ("09/12/2026, 10:00", Locale(tz="UTC"), None),
    ("2026-03-29T02:30:00", Locale(tz="Europe/Amsterdam"), None),
    ("2026-10-25T02:30:00", Locale(tz="Europe/Amsterdam"), None),
])
def test_deadlines_respect_locale_and_leave_ambiguous_times_unset(
    value: str, locale: Locale, expected: str | None
) -> None:
    raw = RawTender(url=_MOCK_ORIGIN, fields={"id": "X", "submissionDeadline": value})
    tender = normalize(raw, locale)
    assert tender is not None
    assert tender.submissionDeadline == expected


def test_normalizer_maps_optional_fields_and_keeps_withdrawal_status() -> None:
    raw = RawTender(url=_MOCK_ORIGIN, fields={
        "id": "X",
        "publicationDate": "20 novembre 2026",
        "submissionDeadline": "2020-01-01T10:00:00Z",
        "status": "Annulé",
        "procedureType": "Appel d’offres ouvert",
        "contractNature": "Travaux",
        "cpvCodes": ["45233140-2", "45233140", "90919200-4"],
        "placeOfPerformance.city": "Paris",
        "placeOfPerformance.countryCode": "fr",
    })
    tender = normalize(raw, Locale(lang="fr"))
    assert tender is not None
    assert tender.publicationDate == "2026-11-20"
    assert tender.status == "withdrawn"
    assert tender.procedureType == "open"
    assert tender.contractNature == "works"
    assert tender.cpvCodes == ["45233140", "90919200"]
    assert tender.api_dict()["placeOfPerformance"] == {"city": "Paris", "countryCode": "FR"}


def test_cyprus_list_only_recipe_preserves_notice_links():
    url = "https://www.eprocurement.gov.cy/epps/quickSearchAction.do?latest=true"
    fixture = Path(__file__).parents[1] / "testset/fixtures/etenders-cy/list.html"
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//table/tbody/tr"},
        "fields": {"id": "normalize-space(td[3])", "title": "normalize-space(td[2]/a)"},
    })
    rows = asyncio.run(_read(recipe, {url: fixture.read_text(encoding="utf-8")}))
    assert len(rows) == 5
    for row in rows:
        assert row.url == "https://www.eprocurement.gov.cy/epps/cft/prepareViewCfTWS.do?resourceId=" + row.fields["id"]


def test_ambiguous_title_links_keep_listing_url_and_explicit_field_wins():
    url = "https://example.test/list"
    page = '<article><a href="/one">Bridge</a><a href="/two">Bridge</a></article>'
    recipe = Recipe.model_validate({
        "list": {"url": url, "item": "//article"},
        "fields": {"title": "string(a[1])"},
    })
    assert asyncio.run(_read(recipe, {url: page}))[0].url == url
    recipe.fields["url"] = "a[2]/@href"
    assert asyncio.run(_read(recipe, {url: page}))[0].url == "https://example.test/two"
