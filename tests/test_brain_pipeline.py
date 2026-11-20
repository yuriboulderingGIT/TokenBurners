"""The worker learns from real fixture pages and repairs a changed layout."""

import asyncio
import json
from pathlib import Path

import httpx

from app.db import DB
from app.fetcher import Fetcher
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
                assert "the list selector found no tenders" in evidence["feedback"]
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
