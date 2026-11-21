"""Verify lifecycle evidence, change notifications, operator recovery and stored scope through the worker."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db import DB
from app.fetcher import FakeFetcher
from app.models import Recipe
from app.worker import Worker
from brain.learn import AttachFailed

BASE = "https://portal.example"
LIST = BASE + "/notices?latest=true"


def recipe():
    return Recipe.model_validate({
        "list": {"url": LIST, "item": "//article"}, "detail": {"link": "./a/@href"},
        "fields": {"id": "//h1/@data-id", "title": "//h1", "status": "//span[@class='status']",
                   "submissionDeadline": "//time/@datetime"},
    })


def listing(*ids):
    return "".join(f'<article><a href="/notice/{n}">Road {n}</a></article>' for n in ids)


def detail(n, status, deadline=""):
    return f'<h1 data-id="{n}">Road {n}</h1><span class="status">{status}</span><time datetime="{deadline}"/>'


async def run(worker, portal, action="scheduled"):
    run_id = worker.request_read(portal, action=action)
    await worker._handle(*worker.queue.get_nowait())
    return run_id


def test_missing_detail_recheck_requires_matching_id_and_evidence(tmp_path, monkeypatch):
    # API times have whole seconds; each save gets its own second so changes are ordered.
    ticks = itertools.count()
    start = datetime(2026, 11, 21, 9, tzinfo=timezone.utc)
    monkeypatch.setattr("app.db.now_iso",
                        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"))
    db = DB(tmp_path / "lifecycle.db")
    portal = db.portal_create(LIST)
    db.recipe_save(portal, recipe(), "attach", 0)
    pages = {LIST: listing(1, 2, 3, 4), **{f"{BASE}/notice/{n}": detail(n, "open") for n in range(1, 5)}}
    def row():
        return db.one("SELECT * FROM tenders WHERE portal_id=? AND id='4'", (portal,))

    async def exercise():
        fetch = FakeFetcher(pages)
        worker = Worker(db, fetch, None)
        try:
            await run(worker, portal)
            initial = row()
            assert initial["detail_url"] == BASE + "/notice/4"
            assert initial["source_status"] == "open"
            pages[LIST] = listing(1, 2, 3)
            del pages[BASE + "/notice/4"]
            await run(worker, portal)
            missing = row()
            assert missing["status"] == "open" and missing["missing_since"] and missing["detail_checked_at"]
            assert missing["last_changed_at"] == initial["last_changed_at"]
            assert missing["last_seen_at"] == initial["last_seen_at"]
            assert "Latest" in db.portal_get(portal)["monitoring_scope"]
            assert "3 listed now, 1 kept from earlier reads" in db.portal_get(portal)["reason"]

            pages[BASE + "/notice/4"] = detail("unrelated", "closed")
            await run(worker, portal)
            assert row()["status"] == "open" and row()["missing_since"] == missing["missing_since"]
            pages[BASE + "/notice/4"] = detail(4, "withdrawn")
            await run(worker, portal)
            withdrawn = row()
            assert withdrawn["status"] == withdrawn["source_status"] == "withdrawn"
            assert withdrawn["last_changed_at"] > initial["last_changed_at"]
            assert withdrawn["last_seen_at"] == initial["last_seen_at"]
            assert withdrawn["missing_since"] == missing["missing_since"]
            pages[LIST] = listing(1, 2, 3, 4)
            await run(worker, portal)
            assert row()["missing_since"] is None and row()["last_changed_at"] == withdrawn["last_changed_at"]
            assert len(db.tenders_listed_last(portal)) == 4
        finally:
            await fetch.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["refresh", "repair", "relearn"])
def test_operator_can_recover_after_a_failed_repair(tmp_path, monkeypatch, action):
    db = DB(tmp_path / "repair.db")
    portal = db.portal_create(LIST)
    original = recipe()
    db.recipe_save(portal, original, "attach", 0)
    pages = {LIST: listing(1), BASE + "/notice/1": detail(1, "open")}
    attempts = []
    replacement = recipe()
    replacement.list_.item = "//section"

    async def candidate(*args, **kwargs):
        assert db.portal_get(portal)["state"] == "healing"
        assert db.recipe_latest(portal).version == 1
        assert db.tenders_for(portal)[0]["id"] == "1"
        attempts.append("repair")
        return None if len(attempts) == 1 else replacement

    monkeypatch.setattr("app.worker.repair", candidate)
    monkeypatch.setattr("app.worker.learn_recipe", candidate)
    async def exercise():
        fetch = FakeFetcher(pages)
        worker = Worker(db, fetch, SimpleNamespace(scoped=lambda *args: SimpleNamespace(total_cost=0)))
        try:
            await run(worker, portal)
            pages[LIST] = listing(1).replace("article", "section")
            await run(worker, portal)
            assert db.portal_get(portal)["state"] == "failed" and attempts == ["repair"]
            await run(worker, portal)  # timed retries incur no additional model use
            assert attempts == ["repair"]
            if action == "refresh":
                # Clients may call refresh every few minutes: no second repair within the hour.
                await run(worker, portal, action="refresh")
                assert attempts == ["repair"]
                monkeypatch.setattr("app.worker.REFRESH_REPAIR_MINUTES", 0)
            scheduled = worker.request_read(portal)
            assert worker.request_read(portal, action=action) == scheduled
            await worker._handle(*worker.queue.get_nowait())
            assert attempts == ["repair", "repair"]
            assert db.portal_get(portal)["state"] == "serving"
            assert db.recipe_latest(portal).version == 2
            assert db.recipe_latest(portal).list_.item == "//section"
            assert any(r["kind"] == "repair" and r["result"] == "ok" for r in db.runs_for(portal))
        finally:
            await fetch.aclose()
    asyncio.run(exercise())


def test_refresh_of_a_portal_that_could_not_attach_calls_the_model_at_most_hourly(tmp_path, monkeypatch):
    db = DB(tmp_path / "attach.db")
    portal = db.portal_create(LIST)
    attempts = []

    async def learn(*args, **kwargs):
        attempts.append("attach")
        raise AttachFailed("no tender listing found")

    monkeypatch.setattr("app.worker.learn_recipe", learn)

    async def exercise():
        fetch = FakeFetcher({})
        worker = Worker(db, fetch, SimpleNamespace(scoped=lambda *args: SimpleNamespace(total_cost=0)))
        try:
            await run(worker, portal)
            assert db.portal_get(portal)["state"] == "failed" and attempts == ["attach"]
            reason = db.portal_get(portal)["reason"]
            await run(worker, portal, action="refresh")  # within the hour: no model call
            assert attempts == ["attach"] and db.portal_get(portal)["reason"] == reason
            await run(worker, portal, action="relearn")  # an operator's explicit request is not limited
            assert attempts == ["attach", "attach"]
            monkeypatch.setattr("app.worker.REFRESH_REPAIR_MINUTES", 0)
            await run(worker, portal, action="refresh")
            assert attempts == ["attach", "attach", "attach"]
        finally:
            await fetch.aclose()
    asyncio.run(exercise())
