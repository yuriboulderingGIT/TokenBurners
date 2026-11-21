"""Fixture regressions for attach checks and challenge detection."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
import pytest
from lxml import html

from app.fetcher import FakeFetcher, Fetcher
from app.models import Recipe
from brain.blocks import Blocked, detect_block
from brain.learn import AttachFailed, RecipeValidationError, learn_recipe, read_page_sample, validate_candidate
from brain.run_recipe import MAX_PAGES, RecipeReadError, empty_search_url, portal_origin, run_recipe

_LIST = "http://portal.example/tenders"
_LIST_HTML = """
<ul aria-label="Tender notices">
  <li data-id="P1"><h2>School roof replacement</h2></li>
  <li data-id="P2"><h2>Library lighting installation</h2></li>
  <li data-id="P3"><h2>Road resurfacing</h2></li>
</ul>
"""

_CAPTCHA = '<form><div class="g-recaptcha"></div><button>Continue</button></form>'
_LOGIN = '<form><input type="password"><button>Continue</button></form>'
_IMAGE_GATE = """This question is for testing whether you are a human visitor.
<b>What code is in the image?</b><input type="text" id="ans" name = "answer">"""
_SCRIPT_SHELL = """<html><head><script type="module" src="/app.js"></script></head>
<body><noscript>Enable JavaScript.</noscript><div id="app"></div></body></html>"""


def test_listing_evidence_keeps_portal_issued_identifiers_and_sort_values():
    markup = """<table><tr data-oid="NetTender-27" data-tendersid="10003030"
                        onclick="openTender()">
      <td data-sort-value="2026-11-30 10:00:00">30.11.2026, 10:00h</td>
    </tr></table>"""

    async def sample():
        fetch = FakeFetcher({_LIST: markup})
        try:
            return await read_page_sample(_LIST, fetch.for_portal("portal"), portal_origin(_LIST))
        finally:
            await fetch.aclose()

    evidence = asyncio.run(sample()).markup
    assert 'data-oid="NetTender-27"' in evidence
    assert 'data-tendersid="10003030"' in evidence
    assert 'data-sort-value="2026-11-30 10:00:00"' in evidence
    assert "onclick" not in evidence


def test_large_menu_does_not_hide_main_listing_from_learning():
    menu = "".join(f'<a href="/menu/{i}">Menu item {i}</a>' for i in range(500))
    markup = f'<html><head><link rel="next" href="?page=2"></head><body><nav>{menu}</nav>' \
             '<main><a href="/notices/REF-1">Bridge repairs</a></main></body></html>'

    async def sample():
        fetch = FakeFetcher({_LIST: markup})
        try:
            return await read_page_sample(_LIST, fetch.for_portal("portal"), portal_origin(_LIST))
        finally:
            await fetch.aclose()

    evidence = asyncio.run(sample())
    assert next(iter(evidence.links)) == "http://portal.example/notices/REF-1"
    assert '<a href="/notices/REF-1">Bridge repairs</a>' in evidence.markup
    assert 'rel="next"' in evidence.markup


def test_attach_can_learn_from_the_observed_empty_public_search():
    search = "http://portal.example/results?query=&scope=open"
    form = """<form role="search" method="get" action="/results">
      <input type="search" name="query"><input type="hidden" name="scope" value="open">
      <button type="submit">Find tenders</button>
    </form>"""

    class Locator:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                assert evidence["empty_search_url"] == search
                return {"list_url": search}
            assert evidence["list_page"]["url"] == search
            return {"list": {"url": search, "item": "//li"}, "fields": {"id": "@data-id", "title": "./h2"}}

    async def exercise():
        fetcher = FakeFetcher({_LIST: form, search: _LIST_HTML})
        try:
            recipe = await learn_recipe(_LIST, fetcher.for_portal("fixture"), Locator())
            assert recipe.list_.url == search
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
    listing_url = "http://portal.example/notices/"
    scoped = form.replace('action="/results"', 'action="/notices/results"')
    assert empty_search_url(html.fromstring(scoped + form), listing_url) == (
        "http://portal.example/notices/results?query=&scope=open"
    )
    for unsafe in (form.replace('method="get"', 'method="post"'),
                   form.replace('type="search"', 'type="search" required'),
                   form.replace('action="/results"', 'action="https://unrelated.example/results"')):
        assert empty_search_url(html.fromstring(unsafe), _LIST) is None


def test_attach_keeps_repeated_page_failure_when_later_selectors_miss(monkeypatch):
    markup = _LIST_HTML + '<a rel="next" href="?page=2">Next</a>'
    feedback = []

    class Model:
        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                return {"list_url": _LIST}
            feedback.append(evidence["feedback"])
            return {"list": {"url": _LIST, "item": "//li" if len(feedback) == 1 else "//article",
                             "next_page": None if len(feedback) == 1 else "//a[@rel='next']/@href"}}

    async def no_feed(*_args):
        return []

    monkeypatch.setattr("brain.js_discover._capture_json", no_feed)

    async def exercise():
        fetcher = FakeFetcher({_LIST: markup, _LIST + "?page=2": _LIST_HTML})
        try:
            with pytest.raises(AttachFailed) as failed:
                await learn_recipe(_LIST, fetcher.for_portal("fixture"), Model())
            assert "the next listing page repeated the same notice rows" in str(failed.value)
            assert "the list selector found no tenders" in str(failed.value)
            assert len(feedback[2]) == 2
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_image_challenge_on_next_page_stops_the_read():
    recipe = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//li", "next_page": "//a[@rel='next']/@href"}
    })

    async def exercise():
        pages = {_LIST: _LIST_HTML + '<a rel="next" href="?page=2">Next</a>', _LIST + "?page=2": _IMAGE_GATE}
        fetcher = FakeFetcher(pages)
        try:
            with pytest.raises(Blocked, match="captcha"):
                await run_recipe(recipe, fetcher.for_portal("fixture"))
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
    assert detect_block(_IMAGE_GATE, 200, content_available=True) is None
    assert detect_block('<input name="answer">What code is in the image?', 200) is None


@pytest.mark.parametrize("has_feed", [True, False])
def test_script_shell_checks_feed_without_requesting_html_selectors(monkeypatch, has_feed):
    feed = _LIST + ".json"

    class FeedLLM:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            assert "endpoints" in json.loads(prompt)
            return {"endpoint": 0, "recipe": {"list": {"item": "$.items[*]"},
                                              "fields": {"id": "$.id", "title": "$.title"}}}

    async def capture(url, fetch):
        assert url == _LIST
        return [(feed, payload)] if has_feed else []

    payload = {"items": [{"id": "REF-1", "title": "School repairs"}]}
    monkeypatch.setattr("brain.js_discover._capture_json", capture)

    async def exercise():
        fetcher = FakeFetcher({_LIST: _SCRIPT_SHELL, feed: json.dumps(payload)})
        try:
            attach = learn_recipe(_LIST, fetcher.for_portal("fixture"), FeedLLM())
            if has_feed:
                assert (await attach).list_.url == feed
            else:
                with pytest.raises(AttachFailed, match="requires JavaScript"):
                    await attach
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_landing_links_to_listing_with_script_only_next_control(monkeypatch):
    listing = _LIST + "/open"
    control = """<a href="javascript:;"><span title="Aller à la page suivante"><i></i></span></a>"""

    class Locator:
        async def json(self, prompt, **kwargs):
            assert "entry" in json.loads(prompt)
            return {"list_url": listing}

    async def capture(url, fetch):
        assert url == listing
        return []

    monkeypatch.setattr("brain.js_discover._capture_json", capture)

    async def exercise():
        fetcher = FakeFetcher({_LIST: f'<a href="{listing}">Marchés en cours</a>', listing: _LIST_HTML + control})
        try:
            with pytest.raises(AttachFailed, match="next page control provides no public HTTP link"):
                await learn_recipe(_LIST, fetcher.for_portal("fixture"), Locator())
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("total", [1, 500])
def test_direct_json_entry_cannot_be_misread_as_one_html_row(total):
    feed = {"total_count": total, "results": [{"id": "T1", "title": "School repairs"}]}
    html_recipe = Recipe.model_validate({"list": {"url": _LIST, "item": "/html/body/p"}})

    class FeedLLM:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            assert "endpoints" in json.loads(prompt)
            return {"endpoint": 0, "recipe": {"list": {"item": "$.results[*]"},
                                              "fields": {"id": "$.id", "title": "$.title"}}}

    async def exercise():
        fetcher = FakeFetcher({_LIST: json.dumps(feed)})
        scoped = fetcher.for_portal("fixture")
        try:
            with pytest.raises(RecipeReadError, match="portal returned JSON"):
                await run_recipe(html_recipe, scoped)
            if total == 1:
                assert (await learn_recipe(_LIST, scoped, FeedLLM())).kind == "json_api"
            else:
                with pytest.raises(AttachFailed, match="advertised 500 tenders"):
                    await learn_recipe(_LIST, scoped, FeedLLM())
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_attach_uses_final_origin_for_evidence_and_recipe():
    entry = "http://portal.example/start"
    secure = "https://portal.example/start"
    landing = "https://www.portal.example/home/"
    listing = "https://www.portal.example/home/notices"
    redirects = {entry: secure, secure: landing}

    def respond(request):
        url = str(request.url)
        if url in redirects:
            return httpx.Response(301, headers={"location": redirects[url]})
        return httpx.Response(200, text={landing: '<a href="notices">Tenders</a>', listing: _LIST_HTML}[url])

    class FixtureLLM:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                assert evidence["entry"]["url"] == landing
                assert evidence["entry"]["links"] == {listing: "Tenders"}
                return {"list_url": "notices"}
            assert evidence["list_page"]["url"] == listing
            return {"list": {"url": listing, "item": "//li"},
                    "fields": {"id": "@data-id", "title": "./h2", "submissionDeadline": None}}

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(respond))
        try:
            recipe = await learn_recipe(entry, fetcher.for_portal("fixture"), FixtureLLM())
            assert recipe.list_.url == listing
            assert "submissionDeadline" not in recipe.fields
            assert [url for _, _, url in fetcher.request_log] == [entry, secure, landing, listing, listing]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("destination", [
    "https://other.example", "https://portal.example:8443", "https://user@portal.example",
    "http://www.portal.example", "https://www.portal.example.other.example",
])
def test_attach_rejects_unsafe_redirect_before_request(destination):
    entry = "https://portal.example/start"

    async def exercise():
        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": destination})
        ))
        try:
            with pytest.raises(AttachFailed, match="redirected outside"):
                await learn_recipe(entry, fetcher.for_portal("fixture"), None)
            assert [url for _, _, url in fetcher.request_log] == [entry]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(("markup", "kind"), [
    (_CAPTCHA, "captcha"), (_IMAGE_GATE, "captcha"), (_LOGIN, "login"), ('<input type="password">', "login"),
])
def test_standalone_form_gate_is_detected_without_a_heading(markup, kind):
    assert detect_block(markup, 200) == kind


@pytest.mark.parametrize("widget", [_CAPTCHA, f"<footer><h2>Sign in</h2>{_LOGIN}</footer>"])
def test_short_public_list_with_incidental_forms_can_be_sampled(widget):
    async def sample():
        fetcher = FakeFetcher({_LIST: '<a href="/procurement/1">School roof</a>' + widget})
        try:
            origin = portal_origin(_LIST)
            assert origin is not None
            page = await read_page_sample(_LIST, fetcher.for_portal("fixture"), origin)
            assert page.links == {"http://portal.example/procurement/1": "School roof"}
        finally:
            await fetcher.aclose()

    asyncio.run(sample())


@pytest.mark.parametrize(
    ("budget", "title_selector", "expected_documents", "failure"),
    [
        (35, "./h2", None, None),
        (MAX_PAGES + 1, "./h2", None, None),  # capped to the limit and checked on the real pages (test_page_limit)
        (0, "./h2", None, "max_pages"),
        (35, "./h3", None, "ID and a title"),
        (35, "./h2", {"P2"}, "still have no document links"),
        (35, "./h2", {"beyond-sample"}, None),
    ],
)
def test_validation_accepts_sparse_public_tenders_and_rejects_known_bad_evidence(
    budget, title_selector, expected_documents, failure
):
    recipe = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//li", "max_pages": budget},
        "fields": {"id": "./@data-id", "title": title_selector},
    })

    async def check():
        fetcher = FakeFetcher({_LIST: _LIST_HTML})
        try:
            await validate_candidate(
                recipe, fetcher.for_portal("fixture"), expected_document_ids=expected_documents
            )
        finally:
            await fetcher.aclose()

    if failure:
        with pytest.raises(RecipeValidationError, match=failure):
            asyncio.run(check())
    else:
        asyncio.run(check())


def test_attach_verifies_21_pages_with_sparse_fields_and_footer_captcha():
    page_urls = [_LIST, *[f"{_LIST}?page={number}" for number in range(2, 22)]]
    detail_urls = [f"http://portal.example/notice/{number}" for number in range(1, 22)]
    pages = {}
    for index, (page_url, detail_url) in enumerate(zip(page_urls, detail_urls, strict=True)):
        next_page = (
            f'<a rel="next" href="{page_urls[index + 1]}">Next</a>'
            if index + 1 < len(page_urls) else ""
        )
        detail_path = urlsplit(detail_url).path
        pages[page_url] = (
            f'<h1>Public tenders</h1><ul><li><a href="{detail_path}">Notice {index + 1}</a></li></ul>'
            f"{next_page}<footer><h2>CAPTCHA</h2>{_CAPTCHA}</footer>"
        )
        pages[detail_url] = f'<h1>Notice {index + 1}</h1><p id="reference">P{index + 1}</p>'

    class FixtureLLM:
        model_name = "fixture-mini"

        def __init__(self):
            self.calls = []

        async def json(self, prompt, *, system, strong=False):
            assert not strong
            evidence = json.loads(prompt)
            self.calls.append(evidence)
            if "entry" in evidence:
                assert _LIST == evidence["entry"]["url"]
                return {"list_url": _LIST, "detail_url": detail_urls[0]}
            assert "at most 100" in evidence["task"]
            assert detail_urls[0] == evidence["detail_page"]["url"]
            return {
                "list": {
                    "url": _LIST, "item": "//li", "next_page": "//a[@rel='next']/@href",
                    "max_pages": 21,
                },
                "detail": {"link": ".//a/@href"},
                "fields": {"id": "//*[@id='reference']", "title": "//h1"},
            }

    async def exercise():
        fetcher = FakeFetcher(pages)
        llm = FixtureLLM()
        try:
            result = await learn_recipe(_LIST, fetcher.for_portal("fixture"), llm)
            assert result.list_.max_pages == 21
            assert len(llm.calls) == 2
            requested = [url for _, _, url in fetcher.request_log]
            assert set(requested) == set(page_urls + detail_urls[:3])
            assert requested[-1] == page_urls[-1]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_navigation_follows_observed_intermediate_and_preserves_notice_attributes():
    menu = _LIST + "/menu"
    listing = _LIST + "/avis"
    urls = { _LIST: '<nav><a href="tenders/menu">Marchés publics</a></nav>',
             menu: '<main><a href="avis">Avis en cours</a></main>',
             listing: '<table><tr data-oid="FR-1"><td>École</td></tr></table>' }

    class Navigator:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            data = json.loads(prompt)
            if "entry" in data:
                if data["entry"]["url"] == _LIST:
                    return {"list_url": menu, "intermediate": True}
                return {"list_url": listing}
            assert 'data-oid="FR-1"' in data["list_page"]["sample_row"]
            return {"list": {"url": listing, "item": "//tr"}, "fields": {"id": "@data-oid", "title": "./td"}}

    async def check():
        fetcher = FakeFetcher(urls)
        try:
            recipe = await learn_recipe(_LIST, fetcher.for_portal("test"), Navigator())
            assert recipe.list_.url == listing
        finally:
            await fetcher.aclose()
    asyncio.run(check())


@pytest.mark.parametrize("label,finished", [("Pagina 2 di 2", True), ("2 / 2", True), ("Page 2 of 5", False)])
def test_placeholder_only_ends_numbered_pager_on_last_page(label, finished):
    from lxml import html
    from brain.run_recipe import advertised_next_urls, NoPublicNextLink
    doc = html.fromstring(f'<nav><a href="?page=1">1</a><a rel="next" href="#">»</a><span>{label}</span></nav>')
    if finished:
        assert not advertised_next_urls(doc, _LIST)
    else:
        with pytest.raises(NoPublicNextLink):
            advertised_next_urls(doc, _LIST)


def test_pagination_rejects_new_url_returning_identical_notice_rows():
    async def exercise():
        second = _LIST + "?page=2"
        fetcher = FakeFetcher({_LIST: _LIST_HTML + '<a rel="next" href="?page=2">2</a>', second: _LIST_HTML})
        recipe = Recipe.model_validate({"list": {"url": _LIST, "item": "//li", "next_page": "//a[@rel='next']/@href"},
                                        "fields": {"id": "@data-id", "title": "./h2"}})
        try:
            with pytest.raises(RecipeReadError, match="repeated the same notice rows"):
                await run_recipe(recipe, fetcher.for_portal("test"), max_tenders=1, verify_all_pages=True)
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
