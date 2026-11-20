"""Fixture regressions for attach checks and challenge detection."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
import pytest

from app.fetcher import FakeFetcher, Fetcher
from app.models import Recipe
from brain.blocks import detect_block
from brain.learn import AttachFailed, RecipeValidationError, learn_recipe, read_page_sample, validate_candidate
from brain.run_recipe import MAX_PAGES, RecipeReadError, portal_origin, run_recipe

_LIST = "http://portal.example/tenders"
_LIST_HTML = """
<ul>
  <li data-id="P1"><h2>School roof replacement</h2></li>
  <li data-id="P2"><h2>Library lighting installation</h2></li>
  <li data-id="P3"><h2>Road resurfacing</h2></li>
</ul>
"""

_CAPTCHA = '<form><div class="g-recaptcha"></div><button>Continue</button></form>'
_LOGIN = '<form><input type="password"><button>Continue</button></form>'


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
            return {"list": {"url": listing, "item": "//li"}, "fields": {"id": "@data-id", "title": "./h2"}}

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(respond))
        try:
            recipe = await learn_recipe(entry, fetcher.for_portal("fixture"), FixtureLLM())
            assert recipe.list_.url == listing
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
    (_CAPTCHA, "captcha"), (_LOGIN, "login"), ('<input type="password">', "login"),
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
        (MAX_PAGES + 1, "./h2", None, "max_pages"),
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
