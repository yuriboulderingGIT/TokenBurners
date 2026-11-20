"""Worker rules. OWNER: A1."""

from app.db import db
from app.worker import Worker


def test_refresh_while_read_is_queued_returns_same_run_id() -> None:
    worker = Worker(db, fetcher=None, llm=None)  # not started: reads stay queued
    portal_id = db.portal_create("https://portal.example/worker-test")
    first = worker.request_read(portal_id)
    assert worker.request_read(portal_id) == first
    assert worker.queue.qsize() == 1


# ----- retrying failed portals -----

import asyncio  # noqa: E402

from app import states  # noqa: E402
from app.fetcher import FakeFetcher  # noqa: E402
from app.models import Health, RawTender, Recipe, Tender  # noqa: E402

RECIPE = Recipe.model_validate({"list": {"url": "https://portal.example/list", "item": "//tr"}})


def _failed_portal(url: str) -> str:
    portal_id = db.portal_create(url)
    db.recipe_save(portal_id, RECIPE, "attach", 0.0)
    states.set_state(db, portal_id, states.FAILED, "blocked: captcha")
    return portal_id


def _run_one(worker: Worker, portal_id: str) -> str:
    run_id = worker.request_read(portal_id)
    asyncio.run(worker._handle(*worker.queue.get_nowait()))
    return run_id


def _patch_read(monkeypatch, healthy: bool) -> None:
    tender = Tender(id="T1", url="https://portal.example/t1", title="Roads", buyerName="City",
                    publicationDate="2026-11-20", submissionDeadline=None, status="open")

    async def fake_run_recipe(recipe, fetch):
        return [RawTender(url=tender.url)]

    async def no_repair(*args, **kwargs):
        raise AssertionError("a retry must never call repair (model cost)")

    monkeypatch.setattr("app.worker.run_recipe", fake_run_recipe)
    monkeypatch.setattr("app.worker.normalize", lambda raw, locale: tender)
    monkeypatch.setattr("app.worker.check_health", lambda prev, new: Health(ok=healthy, problems=[] if healthy else ["no tenders found"]))
    monkeypatch.setattr("app.worker.repair", no_repair)


def test_failed_portal_is_due_only_after_its_retry_interval() -> None:
    worker = Worker(db, fetcher=None, llm=None)
    portal_id = _failed_portal("https://portal.example/due-test")
    assert worker._due(portal_id, 180)  # never read yet
    run_id = db.run_create(portal_id, "read")
    assert not worker._due(portal_id, 180)
    db.execute("UPDATE runs SET started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (run_id,))
    assert worker._due(portal_id, 180)


def test_retry_brings_a_recovered_portal_back_to_serving(monkeypatch) -> None:
    _patch_read(monkeypatch, healthy=True)
    worker = Worker(db, fetcher=FakeFetcher({}), llm=None)
    portal_id = _failed_portal("https://portal.example/recovers")
    run_id = _run_one(worker, portal_id)
    assert db.portal_get(portal_id)["state"] == states.SERVING
    assert db.one("SELECT result FROM runs WHERE id = ?", (run_id,))["result"] == "ok"


def test_retry_that_still_fails_stays_failed_without_repair(monkeypatch) -> None:
    _patch_read(monkeypatch, healthy=False)
    worker = Worker(db, fetcher=FakeFetcher({}), llm=None)
    portal_id = _failed_portal("https://portal.example/still-broken")
    _run_one(worker, portal_id)
    portal = db.portal_get(portal_id)
    assert portal["state"] == states.FAILED
    assert portal["reason"].startswith("still failing on retry")
    assert not db.all("SELECT id FROM runs WHERE portal_id = ? AND kind = 'repair'", (portal_id,))
    assert portal_id not in worker.active


# ----- failed portals that never attached -----

from types import SimpleNamespace  # noqa: E402

from brain.learn import AttachFailed  # noqa: E402


def _never_attached_portal(url: str) -> str:
    portal_id = db.portal_create(url)
    states.set_state(db, portal_id, states.FAILED, "could not attach: no usable tender listing found")
    return portal_id


def test_timed_retry_skips_a_portal_that_never_attached(monkeypatch) -> None:
    async def no_attach(*args, **kwargs):
        raise AssertionError("a timed retry must never ask the model to attach (model cost)")

    monkeypatch.setattr("app.worker.learn_recipe", no_attach)
    worker = Worker(db, fetcher=FakeFetcher({}), llm=None)
    portal_id = _never_attached_portal("https://portal.example/never-attached")
    assert worker._due(portal_id, 180)  # due by the clock, but there is no recipe to re-read
    worker._queue_due_reads()
    assert portal_id not in worker.active
    assert not db.runs_for(portal_id)


def test_timed_retry_still_rereads_a_failed_portal_with_a_recipe() -> None:
    worker = Worker(db, fetcher=None, llm=None)  # not started: reads stay queued
    portal_id = _failed_portal("https://portal.example/timed-retry")
    worker._queue_due_reads()
    assert portal_id in worker.active


def test_manual_refresh_tries_the_attach_again(monkeypatch) -> None:
    attempts = []

    async def failing_attach(url, fetch, llm):
        attempts.append(url)
        raise AttachFailed("no usable tender listing found")

    class OneShotModel:
        def scoped(self, *args):
            return SimpleNamespace(total_cost=0.0)

    monkeypatch.setattr("app.worker.learn_recipe", failing_attach)
    worker = Worker(db, fetcher=FakeFetcher({}), llm=OneShotModel())
    portal_id = _never_attached_portal("https://portal.example/manual-reattach")
    _run_one(worker, portal_id)  # what POST /portals/{id}/refresh queues
    assert attempts == ["https://portal.example/manual-reattach"]
    assert db.portal_get(portal_id)["state"] == states.FAILED


# ----- repair path -----

def _serving_portal(url: str) -> str:
    portal_id = db.portal_create(url)
    db.recipe_save(portal_id, RECIPE, "attach", 0.0)
    states.set_state(db, portal_id, states.SERVING, "delivering 1 tenders")
    return portal_id


def _kind(run_id: str) -> str:
    return db.one("SELECT kind FROM runs WHERE id = ?", (run_id,))["kind"]


def test_refresh_during_repair_and_reread_returns_the_live_run(monkeypatch) -> None:
    worker = Worker(db, fetcher=FakeFetcher({}), llm=type("L", (), {"scoped": lambda *a: type("S", (), {"total_cost": 0.0})()})())
    portal_id = _serving_portal("https://portal.example/repair-live-run")
    seen: list[str] = []
    calls = 0

    async def fake_run_recipe(recipe, fetch):
        nonlocal calls
        calls += 1
        if calls == 2:  # the re-read after the repair
            seen.append(worker.request_read(portal_id))
            raise RuntimeError("connection reset")  # not a recipe problem
        return []

    async def fake_repair(*args, **kwargs):
        seen.append(worker.request_read(portal_id))
        return RECIPE

    monkeypatch.setattr("app.worker.run_recipe", fake_run_recipe)
    monkeypatch.setattr("app.worker.check_health", lambda prev, new: Health(ok=False, problems=["no tenders found"]))
    monkeypatch.setattr("app.worker.repair", fake_repair)
    first = _run_one(worker, portal_id)

    assert [_kind(r) for r in seen] == ["repair", "read"]
    assert first not in seen
    # A crash in the re-read must not leave the portal stuck in healing.
    portal = db.portal_get(portal_id)
    assert portal["state"] == states.FAILED
    assert portal["reason"].startswith("re-read after repair failed")
    assert portal_id not in worker.active
    assert worker.queue.qsize() == 0  # refreshes during the run queued nothing extra


def test_repair_that_crashes_ends_failed_not_healing(monkeypatch) -> None:
    worker = Worker(db, fetcher=FakeFetcher({}), llm=type("L", (), {"scoped": lambda *a: type("S", (), {"total_cost": 0.0})()})())
    portal_id = _serving_portal("https://portal.example/repair-crash")

    async def fake_run_recipe(recipe, fetch):
        return []

    async def crashing_repair(*args, **kwargs):
        raise ValueError("model answered nonsense")

    monkeypatch.setattr("app.worker.run_recipe", fake_run_recipe)
    monkeypatch.setattr("app.worker.check_health", lambda prev, new: Health(ok=False, problems=["no tenders found"]))
    monkeypatch.setattr("app.worker.repair", crashing_repair)
    _run_one(worker, portal_id)

    portal = db.portal_get(portal_id)
    assert portal["state"] == states.FAILED
    assert portal["reason"].startswith("repair crashed")
    repair_run = db.one("SELECT result, finished_at FROM runs WHERE portal_id = ? AND kind = 'repair'", (portal_id,))
    assert repair_run["result"] == "failed" and repair_run["finished_at"]


def test_crashed_normal_read_while_healing_keeps_healing(monkeypatch) -> None:
    """E.g. resumed after a restart: not the re-read after a repair, so no jump to failed."""
    worker = Worker(db, fetcher=FakeFetcher({}), llm=None)
    portal_id = _serving_portal("https://portal.example/healing-restart")
    states.set_state(db, portal_id, states.HEALING, "repairing after: no tenders found")

    async def crashing_run_recipe(recipe, fetch):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.worker.run_recipe", crashing_run_recipe)
    _run_one(worker, portal_id)
    portal = db.portal_get(portal_id)
    assert portal["state"] == states.HEALING
    assert "serving last good tenders" in portal["reason"]


def test_attach_failure_with_empty_message_still_explains_state(monkeypatch):
    from brain.learn import AttachFailed

    async def fail(*args):
        raise AttachFailed("")

    monkeypatch.setattr("app.worker.learn_recipe", fail)
    model = type("FixtureModel", (), {"scoped": lambda *args: None})()
    worker = Worker(db, fetcher=FakeFetcher({}), llm=model)
    portal_id = db.portal_create("https://portal.example/empty-feedback")
    _run_one(worker, portal_id)
    assert db.portal_get(portal_id)["reason"] == "could not attach: no working extraction recipe was found"
