"""Regressions for output/site-reading-audit.md (cookies, headers, POST replies, <base>, ids, retries)."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.db import DB
from app.fetcher import FakeFetcher, Fetcher, PortalCancelled, RunPageCache
from app.health import check_health
from app.models import RawTender, ReadResult, Recipe
from app.worker import Worker
from brain import js_discover
from brain.learn import validate_candidate
from brain.normalize import normalize
from brain.run_recipe import PaginationError, run_recipe
from tests.test_js_discover import require_chromium

BASE = 'https://portal.example'


def test_cookie_isolation():
    def respond(request):
        if request.url.path.startswith('/select/'):
            return httpx.Response(200, headers={'set-cookie': f'buyer={request.url.path.rsplit("/", 1)[1]}; Path=/'})
        return httpx.Response(200, text=request.headers.get('cookie', ''))
    async def exercise():
        client = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            one, two = client.for_portal('one'), client.for_portal('two')
            await one.get(BASE + '/select/A')
            await two.get(BASE + '/select/B')
            assert (await one.get(BASE + '/results')).text == 'buyer=A'
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_html_base_url():
    async def exercise():
        client = FakeFetcher({
            BASE + '/list': '<html><head><base href="/procurement/"></head><body><article><a href="notice/1">Roof</a></article></body></html>',
            BASE + '/procurement/notice/1': '<article data-id="1"><h1>Roof</h1></article>',
        })
        recipe = Recipe.model_validate({'list': {'url': BASE + '/list', 'item': '//article'},
            'detail': {'link': './/a/@href'}, 'fields': {'id': '//article/@data-id', 'title': '//h1'}})
        try:
            rows = await run_recipe(recipe, client.for_portal('test'))
            assert [row.fields['id'] for row in rows] == ['1']
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_duplicate_after_first_three_rows():
    async def exercise():
        records = [{'id': str(min(n, 9)), 'title': f'Contract {n}'} for n in range(1, 11)]
        client = FakeFetcher({BASE + '/api': json.dumps({'items': records, 'total': 10})})
        recipe = Recipe.model_validate({'kind': 'json_api', 'list': {'url': BASE + '/api', 'item': '$.items[*]'},
            'fields': {'id': '$.id', 'title': '$.title'}})
        try:
            for read in (validate_candidate, run_recipe):
                with pytest.raises(PaginationError, match='9 distinct identities in 10 entries'):
                    await read(recipe, client.for_portal('test'))
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_first_fetch_transient_error_is_retried(tmp_path):
    async def exercise():
        db = DB(tmp_path / 'retry.db')
        portal_id = db.portal_create(BASE + '/list')
        client = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        worker = Worker(db, client, SimpleNamespace(scoped=lambda *args: SimpleNamespace(total_cost=0)))
        try:
            await worker._handle(portal_id, db.run_create(portal_id, 'read'))
            db.execute("UPDATE runs SET started_at='2000-01-01T00:00:00+00:00'")
            worker._queue_due_reads()
            assert not worker.queue.empty(), dict(db.portal_get(portal_id))
        finally:
            await client.aclose()
    asyncio.run(exercise())


@pytest.fixture
def browser_fixture(monkeypatch):
    asyncio.run(require_chromium())
    monkeypatch.setattr(js_discover, '_JSON_WAIT_SECONDS', 0.6)
    monkeypatch.setattr(js_discover, '_EXTRA_CAPTURE_SECONDS', 0.1)


def capture(script, respond=None, headers=None):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if request.url.path == '/list':
            return httpx.Response(200, text=f'<html><body><script>{script}</script></body></html>',
                headers={'content-type': 'text/html', **(headers or {})})
        if respond:
            return respond(request)
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}]})
    async def exercise():
        client = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        try:
            return await js_discover._capture_json(BASE + '/list', client.for_portal('test'))
        finally:
            await client.aclose()
    return asyncio.run(exercise()), requests


def test_browser_control(browser_fixture):
    captured, requests = capture("fetch('/api/tenders')")
    assert captured, requests


def test_browser_session_cookie(browser_fixture):
    captured, requests = capture("if(document.cookie.includes('session=anonymous')) fetch('/api/tenders');",
        headers={'set-cookie': 'session=anonymous; Path=/; Secure'})
    assert captured, requests


def test_browser_post_response_reaches_page(browser_fixture):
    def respond(request):
        if request.url.path == '/api/query':
            return httpx.Response(200, json={'config': {'language': 'en'}})
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}]})
    captured, requests = capture("fetch('/api/query',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(r=>r.json()).then(()=>fetch('/api/tenders'));", respond)
    assert captured, requests


def test_browser_get_api_subdomain(browser_fixture):
    captured, requests = capture("fetch('https://api.portal.example/tenders')")
    assert captured, requests


def test_browser_requested_json_headers_are_forwarded(browser_fixture):
    def respond(request):
        if request.headers.get('accept') != 'application/json':
            return httpx.Response(200, text='<html>Search form</html>', headers={'content-type': 'text/html'})
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}]})
    captured, requests = capture("fetch('/api/tenders', {headers:{'Accept':'application/json'}})", respond)
    assert captured, requests


def test_browser_cdn_module(browser_fixture):
    def respond(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, text="fetch('https://portal.example/api/tenders');",
                headers={'content-type': 'application/javascript', 'access-control-allow-origin': '*'})
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}]})
    captured, requests = capture("import('https://cdn.example/app.js')", respond)
    assert captured, requests


def test_base_url_applies_to_pagination():
    async def exercise():
        def page(n, following=''):
            return (f'<html><head><base href="/procurement/"></head><body>'
                    f'<article data-id="{n}"><h1>Tender {n}</h1></article>{following}</body></html>')
        client = FakeFetcher({
            BASE + '/tenders': page(1, '<a rel="next" href="tenders?page=2">Next</a>'),
            BASE + '/procurement/tenders?page=2': page(2),
        })
        recipe = Recipe.model_validate({'list': {'url': BASE + '/tenders', 'item': '//article',
            'next_page': '//a[@rel="next"]/@href', 'max_pages': 5}, 'fields': {'id': '@data-id', 'title': './h1'}})
        try:
            rows = await run_recipe(recipe, client.for_portal('test'))
            assert [row.fields['id'] for row in rows] == ['1', '2']
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_browser_script_cookie_reaches_http(browser_fixture):
    def respond(request):
        if 'consent=yes' not in request.headers.get('cookie', ''):
            return httpx.Response(403, text='consent required')
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}]})
    captured, requests = capture("document.cookie='consent=yes; path=/'; fetch('/api/tenders');", respond)
    assert captured, requests


def test_browser_post_search_keeps_anti_forgery_header(browser_fixture):
    def respond(request):
        if request.headers.get('x-csrf-token') != 'abc':
            return httpx.Response(403, text='bad token')
        return httpx.Response(200, json={'tenders': [{'id': '1', 'title': 'Roof'}], 'total': 1})
    captured, requests = capture("fetch('/api/search',{method:'POST',headers:{'content-type':'application/json',"
                                 "'X-CSRF-Token':'abc'},body:'{\"page\":1}'});", respond)
    assert captured and isinstance(captured[0][0], js_discover.ObservedSearch), requests


def test_attach_run_walks_the_listing_once():
    async def exercise():
        pages = {}
        for n in range(1, 51):
            next_link = f'<a rel="next" href="/list?page={n + 1}">Next</a>' if n < 50 else ''
            pages[f'{BASE}/list?page={n}'] = f'<html><body><article data-id="{n}"><h1>Tender {n}</h1></article>{next_link}</body></html>'
        recipe = Recipe.model_validate({'list': {'url': BASE + '/list?page=1', 'item': '//article',
            'next_page': '//a[@rel="next"]/@href', 'max_pages': 100}, 'fields': {'id': '@data-id', 'title': './h1'}})
        client = FakeFetcher(pages)
        try:
            run = RunPageCache(client, 'test')
            await validate_candidate(recipe, run)
            assert len(await run_recipe(recipe, run)) == 50
            assert len(client.request_log) == 50
            await run_recipe(recipe, client.for_portal('test'))  # a later read fetches fresh pages
            assert len(client.request_log) == 100
            client.cancel('test')
            with pytest.raises(PortalCancelled):
                await run.get(BASE + '/list?page=1', follow_redirects=False)
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_attach_run_first_read_reuses_validated_pages(tmp_path, monkeypatch):
    async def exercise():
        db = DB(tmp_path / 'attach.db')
        portal_id = db.portal_create(BASE + '/list')
        client = FakeFetcher({
            BASE + '/list': '<main aria-label="Notices"><article data-id="1"><h1>Roof</h1><a href="/roof.pdf">PDF</a></article></main>',
            BASE + '/roof.pdf': b'%PDF-1.4 roof',
        })
        recipe = Recipe.model_validate({'list': {'url': BASE + '/list', 'item': '//article'},
                                        'fields': {'id': '@data-id', 'title': './h1'},
                                        'documents': {'link': './a/@href'}})
        async def learn(url, fetch, llm):
            await validate_candidate(recipe, fetch)
            return recipe
        monkeypatch.setattr('app.worker.learn_recipe', learn)
        worker = Worker(db, client, SimpleNamespace(scoped=lambda *args: SimpleNamespace(total_cost=0)))
        try:
            await worker._handle(portal_id, db.run_create(portal_id, 'read'))
            assert db.portal_get(portal_id)['state'] == 'serving', dict(db.portal_get(portal_id))
            assert [url for _, _, url in client.request_log if url.endswith('/list')] == [BASE + '/list']
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_notice_repeated_on_overlapping_list_pages_is_healthy():
    # Seen on vergabe.autobahn.de: a list-only notice shifts from page Start=50 to Start=100.
    def tender(n, page):
        return normalize(RawTender(url=f'{BASE}/list?Start={page}', fields={'id': str(n), 'title': f'Contract {n}'}), Recipe.model_validate(
            {'list': {'url': BASE, 'item': '//tr'}}).locale)
    tenders = [tender(n, 50) for n in range(1, 11)] + [tender(10, 100)]
    assert check_health(None, ReadResult(tenders=tenders, raw_count=len(tenders))).ok
