"""A repair preserves portal-issued IDs while the listing changes around the sample."""

import asyncio
import json

import pytest

from app.db import DB
from app.fetcher import FakeFetcher
from app.models import Health, Recipe, Tender
from app.worker import Worker
from brain.repair import repair

_BASE = "https://notices.example"
_LIST = _BASE + "/notices"


def test_direct_repair_without_history_uses_the_observed_row_link():
    recipe = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//article"}, "detail": {"link": ".//a/@href"},
        "fields": {"id": "//code", "title": "//h1"},
    })

    class Model:
        strong_model_name = "fixture"

        async def json(self, prompt, *, system, strong):
            assert json.loads(prompt)["detail_page"]["url"] == _BASE + "/notice/1"
            return recipe.model_dump(by_alias=True)

    async def run():
        fetcher = FakeFetcher({
            _LIST: '<a href="/menu">Menu</a><article><a href="/notice/1">Tender</a></article>',
            _BASE + "/notice/1": "<h1>School roof</h1><code>REF-1</code>",
        })
        try:
            fixed = await repair(recipe, Health(ok=False), fetcher.for_portal("p"), Model())
            assert fixed and fixed.fields == recipe.fields
            assert not any("/menu" in url for _, _, url in fetcher.request_log)
        finally:
            await fetcher.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(("first_probe", "override_url", "expected_archive"), [
    ("missing", False, "C"),
    ("reassigned", False, "C"),
    ("reused_title", False, None),
    ("missing", True, None),
])
def test_turnover_can_supply_a_verified_historical_detail(first_probe, override_url, expected_archive):
    fields = {"id": "//code", "title": "//h1"}
    if override_url:
        fields["url"] = "//a[@rel='canonical']/@href"
    previous = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//li"}, "detail": {"link": ".//a/@href"}, "fields": fields,
    })
    stored = [Tender(
        id=f"REF-{key}", url=f"{_BASE}/archive/{key}", title=f"Tender {key}", buyerName=None,
        publicationDate=None, submissionDeadline=None, status="open",
    ) for key in ("A", "B", "C", "D")]
    # Persisted metadata from an unexpected origin must never become a fetch destination.
    stored.insert(0, stored[0].model_copy(update={"id": "foreign", "url": "https://other.example/notice",
                                                "title": "Foreign tender"}))
    title = "Tender A" if first_probe == "reused_title" else "New season"
    pages = {
        _LIST: f'<article><a href="/current/new">{title}</a></article>',
        _BASE + "/current/new": f'<h2 data-id="NEW">{title}</h2>',
        _BASE + "/archive/B": '<form><input type="password"></form>',
        _BASE + "/archive/C": "<h1>Old template</h1><code>REF-C</code>",
        _BASE + "/archive/D": "<code>REF-D</code>",
    }
    if first_probe == "reassigned":
        pages[_BASE + "/archive/A"] = "<code>Some other tender</code>"
    fetcher = FakeFetcher(pages)

    class Model:
        strong_model_name = "fixture"

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            probes = [url for _, _, url in fetcher.request_log][1:]
            if override_url:
                assert probes == []
            elif first_probe == "reused_title":
                assert probes == [_BASE + "/current/new", _BASE + "/archive/A", _BASE + "/archive/B"]
            else:
                assert probes == [f"{_BASE}/archive/{key}" for key in ("A", "B", "C")]
            if expected_archive:
                assert evidence["detail_page"]["url"] == f"{_BASE}/archive/{expected_archive}"
                assert evidence["previous_id_for_detail_page"] == f"REF-{expected_archive}"
                assert "last healthy read" in evidence["detail_page_context"]
            else:
                assert evidence["detail_page"] is None
            return {
                "list": {"url": _LIST, "item": "//article"}, "detail": {"link": ".//a/@href"},
                "fields": {"id": "//h2/@data-id", "title": "//h2"},
            }

    async def run():
        try:
            candidate = await repair(previous, Health(ok=False), fetcher.for_portal("p"), Model(),
                                     stored_tenders=stored)
            assert candidate and candidate.fields["id"] == "//h2/@data-id"
        finally:
            await fetcher.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(("can_repair", "use_details"), [(False, True), (True, True), (True, False)])
def test_worker_repairs_from_a_stored_tender_after_new_rows_and_navigation(tmp_path, can_repair, use_details):
    db = DB(tmp_path / "identity.sqlite")
    portal = db.portal_create(_LIST)
    previous = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//li"},
        "detail": {"link": ".//a/@href"},
        "fields": {"id": "//code", "title": "//h1"},
    })
    db.recipe_save(portal, previous, "attach", 0)
    pages = {_LIST: "<ul>" + "".join(
        f'<li><a href="/old/{key}">Tender {key}</a></li>' for key in ("A", "B")
    ) + "</ul>"}
    for key in ("A", "B"):
        pages[f"{_BASE}/old/{key}"] = f"<h1>Tender {key}</h1><code>REF-{key}</code>"
    fetcher = FakeFetcher(pages)

    class Model:
        total_cost = 0
        strong_model_name = "fixture"
        attempts = 0

        def scoped(self, *_):
            return self

        async def json(self, prompt, *, system, strong):
            evidence = json.loads(prompt)
            assert strong and db.portal_get(portal)["state"] == "healing"
            assert evidence["detail_page"]["url"] == _BASE + "/new/B"
            assert evidence["previous_id_for_detail_page"] == "REF-B"
            assert "data-portal-key" not in evidence["detail_page"]["html"]
            detail_requests = [url for _, _, url in fetcher.request_log if "/new/" in url]
            assert detail_requests.count(_BASE + "/new/B") == 1
            assert len(detail_requests) == 1 + (3 * self.attempts if use_details else 0)
            if self.attempts:
                assert any("must keep its stored ID 'REF-B'" in msg for msg in evidence["feedback"])
            self.attempts += 1
            attr = "data-portal-key" if can_repair and self.attempts > 1 else "data-row-key"
            return {
                "list": {"url": _LIST, "item": "//article"},
                "detail": {"link": ".//a/@href" if use_details else None},
                "fields": {
                    "id": f"//h2/@{attr}" if use_details else f"@{attr}",
                    "title": "//h2" if use_details else ".//a",
                },
            }

    async def run():
        model = Model()
        worker = Worker(db, fetcher, model)
        try:
            await worker.start()
            worker.request_read(portal)
            await asyncio.wait_for(worker.queue.join(), 5)
            before = db.tenders_for(portal)
            assert [t["id"] for t in before] == ["REF-A", "REF-B"]

            navigation = "".join(f'<a href="/menu/{i}">Menu {i}</a>' for i in range(70))
            keys = ("C", "D", "E", "B", "A")
            pages[_LIST] = navigation + "".join(
                f'<article data-portal-key="REF-{key}" data-row-key="row-{key}">'
                f'<a href="/new/{key}">Tender {key}</a></article>' for key in keys
            )
            for key in keys:
                pages[f"{_BASE}/new/{key}"] = (
                    f'<h2 data-portal-key="REF-{key}" data-row-key="row-{key}">Tender {key}</h2>'
                )
            worker.request_read(portal)
            await asyncio.wait_for(worker.queue.join(), 5)
            assert not any("/menu/" in url for _, _, url in fetcher.request_log)
            if can_repair:
                assert model.attempts == 2 and db.recipe_latest(portal).version == 2
                assert db.portal_get(portal)["state"] == "serving"
                assert {t["id"] for t in db.tenders_for(portal)} == {f"REF-{key}" for key in keys}
            else:
                assert model.attempts == 3 and db.recipe_latest(portal).version == 1
                assert db.portal_get(portal)["state"] == "failed"
                assert db.tenders_for(portal) == before
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("permalinks", [False, True])
def test_repair_checks_json_record_identity_with_reordering_and_turnover(monkeypatch, permalinks):
    fields = {"id": "$.id", "title": "$.title"}
    if permalinks:
        fields["url"] = "$.url"
    previous = Recipe.model_validate({
        "kind": "json_api", "list": {"url": _LIST, "item": "$.items[*]"}, "fields": fields,
    })
    old = [Tender(
        id=key, url=_BASE + "/" + key if permalinks else _LIST, title=title, buyerName=None,
        publicationDate=None, submissionDeadline=None, status="open",
    ) for key, title in [("A", "Road works"), ("B", "Bridge inspection")]]
    # A new year can bring a new notice with the same title. The first proposal also swaps
    # A and B; matching against a bag of old IDs would miss that they changed owners.
    items = [
        {"id": "C", "alternate": "C", "title": "Road works", "url": "/C"},
        {"id": "B", "alternate": "A", "title": "New title" if permalinks else "Bridge inspection", "url": "/B"},
        {"id": "A", "alternate": "B", "title": "Road works", "url": "/A"},
    ]
    attempts = []

    async def rediscover(_url, _fetch, _llm, *, previous_recipe, health, entry_response=None):
        attempts.append(list(health.problems))
        candidate = previous_recipe.model_copy(deep=True)
        if len(attempts) == 1:
            candidate.fields["id"] = "$.alternate"
        return candidate

    monkeypatch.setattr("brain.repair.discover_json_api", rediscover)

    async def run():
        fetcher = FakeFetcher({_LIST: json.dumps({"items": items})})
        try:
            fixed = await repair(
                previous, Health(ok=False, problems=["feed changed"]), fetcher.for_portal("p"), None,
                stored_tenders=old,
            )
            assert fixed is not None and fixed.fields == fields
            assert len(attempts) == 2 and "stored ID 'B'" in attempts[1][-1]
        finally:
            await fetcher.aclose()

    asyncio.run(run())
