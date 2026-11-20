"""Reproductions of weaknesses found in the Friday-evening bug hunt. Each test failed before its fix."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import states
from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker
from brain import llm as llm_module
from brain.llm import LLM, BudgetExceeded

ROOT = Path(__file__).parents[1]
RECIPE = ROOT / "testset/fixtures/mock/recipe_v1.json"


# ---------- 1. The model-answer cache must be off in the container ----------
# Answers were cached before anyone knew whether they were good, so a failed attach replayed
# the same bad answers on every retry (and after delete + re-add) and could never recover.


def test_container_does_not_cache_model_answers() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "LLM_CACHE=0" in dockerfile


def test_config_default_does_not_cache(monkeypatch) -> None:
    import importlib

    from app import config

    monkeypatch.delenv("LLM_CACHE", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore a developer's .env
    try:
        assert importlib.reload(config).LLM_CACHE is False
    finally:
        importlib.reload(config)


# ---------- 2. A budget reservation held by other calls must not fail a call ----------
# Every call reserves its maximum cost first. When parallel calls had reserved the rest of the
# budget, a call failed with BudgetExceeded although real spending was far below the cap,
# and the portal went to `failed` for FAILED_RETRY_MINUTES.


class _FakeResponses:
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.gate = gate

    async def create(self, **_kwargs):
        self.calls += 1
        if self.gate is not None and self.calls == 1:
            await self.gate.wait()  # the first call is slow, the way a real model call is
        usage = SimpleNamespace(input_tokens=100, output_tokens=20, input_tokens_details=None)
        return SimpleNamespace(usage=usage, output_text=json.dumps({"ok": True}))


def _llm(db: DB, budget: float, gate: asyncio.Event | None = None) -> tuple[LLM, _FakeResponses]:
    model = LLM(db, budget_usd=budget, cheap_model="gpt-5-mini", strong_model="gpt-5", cache_dir=None)
    responses = _FakeResponses(gate)
    model._client = SimpleNamespace(responses=responses)
    return model, responses


def test_call_waits_for_a_parallel_call_instead_of_failing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "RESERVATION_WAIT_SECONDS", 5)
    db = DB(tmp_path / "budget.sqlite")

    async def run():
        gate = asyncio.Event()
        # Room for one call's maximum cost (output cap + this tiny prompt on gpt-5-mini), not for two.
        one_call = (llm_module.MAX_OUTPUT_TOKENS * 2.00 + 2_000 * 0.25) / 1e6
        model, responses = _llm(db, budget=1.5 * one_call, gate=gate)
        first = asyncio.create_task(model.scoped("p1", None, "attach").json("prompt"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(model.scoped("p2", None, "attach").json("prompt"))
        await asyncio.sleep(0.3)
        assert not second.done()  # waiting for the first call's reservation, not failed
        gate.set()
        return await first, await second, responses.calls

    assert asyncio.run(run()) == ({"ok": True}, {"ok": True}, 2)


def test_leftover_reservation_from_a_crash_is_not_waited_for(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "RESERVATION_WAIT_SECONDS", 5)
    db = DB(tmp_path / "budget.sqlite")
    model, responses = _llm(db, budget=1.0)
    assert db.llm_try_reserve("old-run", None, 0.999, 1.0)  # left by a process that died mid-call

    start = time.monotonic()
    with pytest.raises(BudgetExceeded):
        asyncio.run(model.scoped("p1", None, "attach").json("prompt"))
    assert responses.calls == 0
    assert time.monotonic() - start < 1


def test_budget_that_is_really_spent_fails_at_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "RESERVATION_WAIT_SECONDS", 5)
    db = DB(tmp_path / "budget.sqlite")
    model, responses = _llm(db, budget=1.0)
    db.llm_log("old", None, "gpt-5", 1, 1, 0.999, "attach")  # already spent

    start = time.monotonic()
    with pytest.raises(BudgetExceeded):
        asyncio.run(model.scoped("p1", None, "attach").json("prompt"))
    assert responses.calls == 0
    assert time.monotonic() - start < 1


# ---------- 3. A temporary error during a portal's first read must not fail it for hours ----------
# With nothing served yet, any network error or HTTP 5xx in the first read sent the portal
# straight to `failed`, retried only after FAILED_RETRY_MINUTES (default 180).


def _portal_with_recipe(tmp_path) -> tuple[DB, str]:
    db = DB(tmp_path / "first-read.sqlite")
    portal_id = db.portal_create("http://portal.example/tenders")
    recipe = Recipe.model_validate(json.loads(RECIPE.read_text(encoding="utf-8")))
    recipe.list_.url = "http://portal.example/tenders"
    db.recipe_save(portal_id, recipe, "attach", 0)
    return db, portal_id


def _read_with(db: DB, portal_id: str, handler) -> None:
    async def run():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        worker = Worker(db, fetcher, llm=None)
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=10)
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(run())


def test_temporary_error_in_first_read_keeps_integrating_then_fails_after_3_tries(tmp_path) -> None:
    db, portal_id = _portal_with_recipe(tmp_path)
    unavailable = lambda request: httpx.Response(503)

    for attempt in (1, 2):
        _read_with(db, portal_id, unavailable)
        row = db.portal_get(portal_id)
        assert row["state"] == states.INTEGRATING, f"attempt {attempt}"
        assert "HTTP 503" in row["reason"] and "try again" in row["reason"]

    _read_with(db, portal_id, unavailable)
    row = db.portal_get(portal_id)
    assert row["state"] == states.FAILED
    assert "3 tries" in row["reason"]


def test_integrating_portal_is_retried_soon(tmp_path, monkeypatch) -> None:
    db, portal_id = _portal_with_recipe(tmp_path)
    monkeypatch.setattr("app.worker.INTEGRATING_RETRY_MINUTES", 5)
    worker = Worker(db, fetcher=None, llm=None)
    run_id = db.run_create(portal_id, "read")
    db.run_finish(run_id, "failed")
    db.execute("UPDATE runs SET started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (run_id,))
    assert worker._retry_minutes(db.portal_get(portal_id)) == 5
    assert worker._due(portal_id, 5)
