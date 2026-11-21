"""Offline review probes. P1 checks assert the corrected behavior; P2 probes retain the historical baseline.

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
from brain.public_search import ObservedSearch, PublicVisit, SearchPages, operation_id
from brain.repair import repair
from brain.run_recipe import RecipeReadError, run_recipe
import pytest

BASE = 'https://portal.example'


def tender(n, **fields):
    return normalize(RawTender(url=f'{BASE}/notice/{n}', fields={
        'id': str(n), 'title': f'Notice {n}', **fields,
    }), Locale())


def test_category_directory_cannot_attach_or_publish():
    async def exercise():
        markup = '<main>' + ''.join(
            f'<article data-id="{i}"><a href="/categoryOverview.do?code={i}">Category {i}</a></article>'
            for i in range(5)
        ) + '</main>'
        fetcher = FakeFetcher({BASE: markup})
        recipe = Recipe.model_validate({'list': {'url': BASE, 'item': '//article'},
                                        'fields': {'id': '@data-id', 'title': './a'}})
        try:
            with pytest.raises(RecipeReadError, match='notice'):
                await validate_candidate(recipe, fetcher.for_portal('test'))
            with pytest.raises(RecipeReadError, match='notice'):
                await run_recipe(recipe, fetcher.for_portal('test'))
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_valid_empty_collection_is_always_unhealthy():
    assert not check_health(None, ReadResult(tenders=[], raw_count=0)).ok


def test_missing_minority_is_closed_and_its_absence_recorded(tmp_path):
    # The brief: "closed once ... the portal no longer lists the tender". The absence is recorded.
    before = ReadResult(tenders=[tender(i) for i in range(4)], raw_count=4)
    after = ReadResult(tenders=before.tenders[:3], raw_count=3)
    assert check_health(before, after).ok
    db = DB(tmp_path / 'closure.db')
    portal = db.portal_create(BASE)
    db.save_tenders(portal, before.tenders)
    db.save_tenders(portal, after.tenders)
    missing = next(t for t in db.tenders_for(portal) if t['id'] == '3')
    assert missing['status'] == 'closed'
    assert db.one('SELECT missing_since FROM tenders WHERE portal_id = ? AND id = ?', (portal, '3'))['missing_since']


def test_latest_window_keeps_a_missing_notice_open(tmp_path):
    # A latest-results window drops notices that are still open: absence alone does not close them.
    before = [tender(i) for i in range(4)]
    db = DB(tmp_path / 'window.db')
    portal = db.portal_create(BASE)
    db.save_tenders(portal, before, results_window=True)
    db.save_tenders(portal, before[:3], results_window=True)
    missing = next(t for t in db.tenders_for(portal) if t['id'] == '3')
    assert missing['status'] == 'open'
    assert db.one('SELECT missing_since FROM tenders WHERE portal_id = ? AND id = ?', (portal, '3'))['missing_since']

def test_twenty_percent_unreadable_ids_can_pass_health():
    assert check_health(None, ReadResult(tenders=[tender(i) for i in range(4)], raw_count=5)).ok


def test_same_document_url_detects_new_revision(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr('app.documents.FILES_DIR', tmp_path / 'files')
        monkeypatch.setattr('app.documents.UNVALIDATED_REFRESH_SECONDS', 0)
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
            assert len(calls) == 2
            assert first.documents[0].sha256 != second.documents[0].sha256
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


def test_manual_failed_refresh_allows_repair(tmp_path):
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
            worker.request_read(portal, action='refresh')
            await worker._handle(*worker.queue.get_nowait())
            assert observed[0]['allow_repair'] is True
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


def test_overlap_cannot_claim_complete_coverage():
    async def exercise():
        def payload(ids):
            return json.dumps({'total': 20, 'items': [{'id': str(i), 'title': f'Notice {i}'} for i in ids]})
        fetcher = FakeFetcher({BASE + '/api?offset=0': payload(range(1, 11)),
                               BASE + '/api?offset=10': payload(range(10, 20))})
        recipe = Recipe.model_validate({'kind': 'json_api',
            'list': {'url': BASE + '/api?offset=0', 'item': '$.items[*]'},
            'fields': {'id': '$.id', 'title': '$.title'}})
        try:
            with pytest.raises(RecipeReadError, match='19 distinct'):
                await run_recipe(recipe, fetcher.for_portal('test'))
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_graphql_reselects_the_intended_operation(monkeypatch):
    async def exercise():
        spec = SearchTransport(endpoint=BASE + '/graphql', media_type='application/json')
        unrelated = ObservedSearch(spec, {'query': 'query { categories { id title } }'}, None)
        intended = ObservedSearch(spec, {'query': 'query { tenders { id title } }'}, None)
        wrong_payload = {'categories': [{'id': 'c1', 'title': 'Construction'}]}
        async def capture(*args, **kwargs):
            return [(unrelated, wrong_payload), (intended, {'tenders': [{'id': 'n1', 'title': 'Roof'}]})]
        monkeypatch.setattr('brain.js_discover._capture_json', capture)
        saved = spec.model_copy(update={'operation_id': operation_id(intended.params)})
        pages = await SearchPages.open(BASE, saved, None)
        assert pages.first_payload['tenders'][0]['id'] == 'n1'
        with pytest.raises(RecipeReadError):
            await SearchPages.open(BASE, spec, None)
    asyncio.run(exercise())


def test_award_status_and_past_date_only_deadline_are_not_open():
    # Fixed on 21 Nov (tests/test_status_words.py): an award label or a past date-only deadline means
    # closed; the date-only deadline itself is still not published with an invented time.
    item = tender(1, status='awarded', submissionDeadline='2020-01-01')
    assert item.status == 'closed' and item.submissionDeadline is None
    assert tender(2, submissionDeadline='2020-01-01').status == 'closed'


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
