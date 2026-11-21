"""Offline diagnostics for the 2026-11-21 review, not desired-behavior regression tests.

Twelve tests PASS when a documented limitation is reproduced. The API-subdomain
repair test checks the fix merged in 84f807f after the original a890ad9 review.
No public HTTP requests or model calls. Run explicitly with pytest.
"""
import asyncio
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ['DATA_DIR'] = str(ROOT / 'tmp' / 'algorithm-review-data')
os.environ['LLM_BUDGET_USD'] = '0'
os.environ['OPENAI_API_KEY'] = ''

import httpx
from app.db import DB
from app.documents import store_documents
from app.fetcher import FakeFetcher, Fetcher
from app.health import check_health
from app.models import Health, Locale, RawTender, ReadResult, Recipe, SearchTransport
from app.worker import Worker
from brain.learn import validate_candidate
from brain.normalize import normalize
from brain.public_search import ObservedSearch, PublicVisit, SearchPages
from brain.repair import repair
from brain.run_recipe import RecipeReadError, run_recipe
import pytest

BASE = 'https://portal.example'


def tender(n, **fields):
    return normalize(RawTender(url=f'{BASE}/notice/{n}', fields={
        'id': str(n), 'title': f'Notice {n}', **fields,
    }), Locale())


def test_category_directory_passes_attach_and_health():
    async def exercise():
        markup = '<main>' + ''.join(
            f'<article data-id="{i}"><a href="/categoryOverview.do?code={i}">Category {i}</a></article>'
            for i in range(5)
        ) + '</main>'
        fetcher = FakeFetcher({BASE: markup})
        recipe = Recipe.model_validate({'list': {'url': BASE, 'item': '//article'},
                                        'fields': {'id': '@data-id', 'title': './a'}})
        try:
            await validate_candidate(recipe, fetcher.for_portal('test'))
            raws = await run_recipe(recipe, fetcher.for_portal('test'))
            result = ReadResult(tenders=[normalize(r, recipe.locale) for r in raws], raw_count=5)
            assert check_health(None, result).ok
            assert all('categoryOverview' in t.url for t in result.tenders)
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_valid_empty_collection_is_always_unhealthy():
    assert not check_health(None, ReadResult(tenders=[], raw_count=0)).ok


def test_missing_minority_is_closed_without_closure_evidence(tmp_path):
    before = ReadResult(tenders=[tender(i) for i in range(4)], raw_count=4)
    after = ReadResult(tenders=before.tenders[:3], raw_count=3)
    assert check_health(before, after).ok
    db = DB(tmp_path / 'missing.db')
    portal = db.portal_create(BASE)
    db.save_tenders(portal, before.tenders)
    db.save_tenders(portal, after.tenders)
    missing = next(t for t in db.tenders_for(portal) if t['id'] == '3')
    assert missing['status'] == 'closed'


def test_twenty_percent_unreadable_ids_can_pass_health():
    assert check_health(None, ReadResult(tenders=[tender(i) for i in range(4)], raw_count=5)).ok


def test_same_document_url_never_revalidated(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr('app.documents.FILES_DIR', tmp_path / 'files')
        db = DB(tmp_path / 'docs.db')
        portal = db.portal_create(BASE)
        calls = []
        def respond(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=b'%PDF-1.4\nrevision-' + str(len(calls)).encode())
        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            first = await store_documents(db, portal, tender(1), [BASE + '/spec.pdf'], fetcher.for_portal(portal))
            second = await store_documents(db, portal, tender(1), [BASE + '/spec.pdf'], fetcher.for_portal(portal))
            assert len(calls) == 1
            assert first.documents[0].sha256 == second.documents[0].sha256
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_api_subdomain_recipe_can_repair_after_upstream_fix(monkeypatch):
    async def exercise():
        recipe = Recipe.model_validate({'kind': 'json_api', 'list': {
            'url': 'https://api.portal.example/tenders', 'item': '$.items[*]'},
            'fields': {'id': '$.id', 'title': '$.title'}})
        fetcher = FakeFetcher({recipe.list_.url: json.dumps({'items': [
            {'id': '1', 'title': 'Roof'}, {'id': '2', 'title': 'Road'}]})})
        discovered = []
        async def discover(url, *args, **kwargs):
            discovered.append(url)
            return recipe
        monkeypatch.setattr('brain.repair.discover_json_api', discover)
        try:
            candidate = await repair(recipe, Health(ok=False, problems=['layout changed']),
                                     fetcher.for_portal('test'), None, portal_url=BASE)
            assert candidate is recipe
            assert discovered == [BASE]
            assert len(fetcher.request_log) == 1
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_manual_failed_refresh_disables_repair(tmp_path):
    async def exercise():
        db = DB(tmp_path / 'repair.db')
        portal = db.portal_create(BASE)
        recipe = Recipe.model_validate({'list': {'url': BASE, 'item': '//article'}})
        db.recipe_save(portal, recipe, 'attach', 0)
        db.portal_update(portal, state='failed')
        fetcher = FakeFetcher({})
        worker = Worker(db, fetcher, None)
        observed = []
        async def read(*args, **kwargs):
            observed.append(kwargs)
        worker._read = read
        try:
            await worker._handle(portal, db.run_create(portal, 'read'))
            assert observed[0]['allow_repair'] is False
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_public_post_offset_is_reset_to_zero():
    async def exercise():
        bodies = []
        def respond(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={'items': [], 'total': 0})
        fetcher = Fetcher(min_interval=0, transport=httpx.MockTransport(respond))
        try:
            search = ObservedSearch(SearchTransport(endpoint=BASE + '/search',
                media_type='application/json', page_field='offset'), {'offset': 100},
                PublicVisit(fetcher.for_portal('test')))
            await search.read(0, 0)
            assert bodies == [{'offset': 0}]
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_overlap_counts_as_complete_but_omits_one_notice():
    async def exercise():
        def payload(ids):
            return json.dumps({'total': 20, 'items': [{'id': str(i), 'title': f'Notice {i}'} for i in ids]})
        fetcher = FakeFetcher({BASE + '/api?offset=0': payload(range(1, 11)),
                               BASE + '/api?offset=10': payload(range(10, 20))})
        recipe = Recipe.model_validate({'kind': 'json_api',
            'list': {'url': BASE + '/api?offset=0', 'item': '$.items[*]'},
            'fields': {'id': '$.id', 'title': '$.title'}})
        try:
            raws = await run_recipe(recipe, fetcher.for_portal('test'))
            rows = [normalize(r, recipe.locale) for r in raws]
            assert len(rows) == 20 and len({t.id for t in rows}) == 19
            assert check_health(None, ReadResult(tenders=rows, raw_count=20)).ok
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_graphql_operations_on_same_endpoint_are_not_distinguished(monkeypatch):
    async def exercise():
        spec = SearchTransport(endpoint=BASE + '/graphql', media_type='application/json')
        unrelated = ObservedSearch(spec, {'query': 'query { categories { id title } }'}, None)
        intended = ObservedSearch(spec, {'query': 'query { tenders { id title } }'}, None)
        wrong_payload = {'categories': [{'id': 'c1', 'title': 'Construction'}]}
        async def capture(*args, **kwargs):
            return [(unrelated, wrong_payload), (intended, {'tenders': [{'id': 'n1', 'title': 'Roof'}]})]
        monkeypatch.setattr('brain.js_discover._capture_json', capture)
        pages = await SearchPages.open(BASE, spec, None)
        assert pages.first_payload == wrong_payload
    asyncio.run(exercise())


def test_unknown_status_and_date_only_deadline_become_open():
    item = tender(1, status='awarded', submissionDeadline='2020-01-01')
    assert item.status == 'open' and item.submissionDeadline is None


def test_json_listing_cannot_follow_html_detail():
    async def exercise():
        fetcher = FakeFetcher({BASE + '/api': json.dumps({'items': [{'url': '/notice/1'}]}),
                               BASE + '/notice/1': '<article data-id="1"><h1>Roof</h1></article>'})
        recipe = Recipe.model_validate({'kind': 'json_api',
            'list': {'url': BASE + '/api', 'item': '$.items[*]'},
            'detail': {'link': '$.url'}, 'fields': {'id': '$.id', 'title': '$.title'}})
        try:
            with pytest.raises(RecipeReadError, match='could not parse portal response as json_api'):
                await run_recipe(recipe, fetcher.for_portal('test'))
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_detail_extraction_discards_list_only_fields():
    async def exercise():
        fetcher = FakeFetcher({BASE: '<article data-id="1"><span class="buyer">Town</span><a href="/notice/1">Roof</a></article>',
            BASE + '/notice/1': '<article data-id="1"><h1>Roof</h1></article>'})
        recipe = Recipe.model_validate({'list': {'url': BASE, 'item': '//article'},
            'detail': {'link': './a/@href'},
            'fields': {'id': '//article/@data-id', 'title': '//h1', 'buyerName': './/span[@class="buyer"]'}})
        try:
            raws = await run_recipe(recipe, fetcher.for_portal('test'))
            assert normalize(raws[0], recipe.locale).buyerName is None
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())
