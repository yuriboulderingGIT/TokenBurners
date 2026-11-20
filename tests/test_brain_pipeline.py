"""The worker learns from real fixture pages and repairs a changed layout."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker
from mock_portal import server

PORTAL_URL = "http://portal.example/tenders"
FIXTURE = Path(__file__).parents[1] / "testset/fixtures/mock/recipe_v1.json"


def _recipe_v2() -> dict:
    return {
        "list": {
            "url": PORTAL_URL,
            "item": "//div[@class='card']",
            "next_page": "//a[@class='pager-next']/@href",
        },
        "detail": {"link": ".//a/@href"},
        "fields": {
            "id": "//th[.='Identifier']/following-sibling::td",
            "title": "//h2[@class='title']",
            "buyerName": "//th[.='Buyer']/following-sibling::td",
            "publicationDate": "//th[.='Publication']/following-sibling::td",
            "submissionDeadline": "//th[.='Closing date']/following-sibling::td",
        },
        "documents": {"link": "//a[@class='doc']/@href"},
        "locale": {"lang": "en", "tz": "Europe/Dublin", "country": "IE"},
    }


def test_worker_attaches_refreshes_and_repairs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE", {"layout": "v1", "variant": "ssr"})
    model_calls = []
    db = DB(tmp_path / "watchtower.sqlite")
    portal_id = db.portal_create(PORTAL_URL)
    v1 = json.loads(FIXTURE.read_text(encoding="utf-8"))
    v1["list"]["url"] = PORTAL_URL

    class FixtureLLM:
        model_name = "fixture-mini"
        strong_model_name = "fixture-strong"
        total_cost = 0.0

        def scoped(self, *_args):
            return self

        async def json(self, prompt, *, system, strong=False):
            evidence = json.loads(prompt)
            model_calls.append(strong)
            if "entry" in evidence:
                return {"list_url": PORTAL_URL, "detail_url": "/tender/MOCK-001"}
            if strong:
                assert db.portal_get(portal_id)["state"] == "healing"
                assert len(db.tenders_for(portal_id)) == 5
                assert evidence["previous_recipe"]["list"]["item"] == v1["list"]["item"]
                if model_calls.count(True) == 1:
                    return v1
                assert any("missed the advertised next page" in problem for problem in evidence["feedback"])
                return _recipe_v2()
            return v1

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, FixtureLLM())
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            assert db.portal_get(portal_id)["state"] == "serving"
            tenders = {row["id"]: row for row in db.tenders_for(portal_id)}
            assert set(tenders) == {f"MOCK-{n:03}" for n in range(1, 6)}
            assert tenders["MOCK-001"]["submissionDeadline"] == "2026-12-09T10:00:00+00:00"
            assert model_calls == [False, False]

            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            assert model_calls == [False, False]

            server.STATE["layout"] = "v2"
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            assert db.portal_get(portal_id)["state"] == "serving"
            assert db.recipe_latest(portal_id).version == 2
            assert db.recipe_latest(portal_id).learned_by == "fixture-strong"
            assert {row["id"] for row in db.tenders_for(portal_id)} == set(tenders)
            assert model_calls == [False, False, True, True]
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("can_repair", [True, False])
def test_changed_second_page_preserves_last_good_tenders(tmp_path, monkeypatch, can_repair):
    db = DB(tmp_path / "pagination.sqlite")
    portal_id = db.portal_create(PORTAL_URL)
    v1 = Recipe.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    v1.list_.url = PORTAL_URL
    db.recipe_save(portal_id, v1, "attach", 0)
    monkeypatch.setattr(server, "STATE", {"layout": "v1", "variant": "ssr"})
    render_list = server.render_list
    render_detail = server.render_detail
    # All fixtures must still be open or an erroneous closure would be invisible.
    def open_detail(tender_id, layout):
        markup = render_detail(tender_id, layout)
        assert markup is not None
        return markup.replace("Dec 2026", "Dec 2098").replace("Jan 2027", "Jan 2099")

    monkeypatch.setattr(server, "render_detail", open_detail)
    last_good = []
    attempts = []

    class RepairLLM:
        total_cost = 0.0
        strong_model_name = "fixture-strong"

        def scoped(self, *_args):
            return self

        async def json(self, prompt, *, system, strong):
            assert strong
            evidence = json.loads(prompt)
            assert db.portal_get(portal_id)["state"] == "healing"
            assert db.tenders_for(portal_id) == last_good
            assert evidence["failing_list_page"]["url"] == PORTAL_URL + "?page=2"
            assert 'class="card"' in evidence["failing_list_page"]["html"]
            attempts.append(evidence)
            candidate = v1.model_dump(by_alias=True)
            if can_repair and len(attempts) > 1:
                candidate["list"]["item"] += " | //div[@class='card']"
            return candidate

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, RepairLLM())
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            last_good.extend(db.tenders_for(portal_id))
            assert len(last_good) == 5 and all(t["status"] == "open" for t in last_good)
            monkeypatch.setattr(server, "render_list",
                                lambda page, layout: render_list(page, "v2" if page == 2 else layout))
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            assert len(attempts) == (2 if can_repair else 3)
            repairs = [r for r in db.runs_for(portal_id) if r["kind"] == "repair"]
            assert len(repairs) == 1
            assert repairs[0]["result"] == ("ok" if can_repair else "failed")
            assert db.portal_get(portal_id)["state"] == ("serving" if can_repair else "failed")
            assert db.recipe_latest(portal_id).version == (2 if can_repair else 1)
            if can_repair:
                stored = db.tenders_for(portal_id)
                assert {t["id"] for t in stored} == {t["id"] for t in last_good}
                assert all(t["status"] == "open" for t in stored)
            else:
                assert db.tenders_for(portal_id) == last_good
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(exercise())
