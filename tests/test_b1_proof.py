"""Prove the B10/B15 reporting path with a local mock and scripted model replies."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

from app.db import DB
from app.fetcher import Fetcher
from app.worker import Worker
from brain import proof
from brain.llm import LLM
from brain.proof_fixture import mock_reply
from mock_portal import server


@pytest.mark.parametrize(("mode", "target"), [("--rehearse", "mock"), ("--live", "all")])
def test_command_uses_local_rehearsal_or_the_existing_live_budget(tmp_path, monkeypatch, mode, target):
    output = tmp_path / "report"
    data_dir = tmp_path / "app-data"
    called = []

    async def capture(target, directory, *, rehearsal, ledger_path):
        called.append((target, directory, rehearsal, ledger_path))
        return True

    monkeypatch.setenv("OPENAI_API_KEY", "unused-fixture-key")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(proof.os, "environ", os.environ.copy())
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
    monkeypatch.setattr(sys, "argv", ["proof", mode, "--output", str(output)])
    monkeypatch.setattr(proof, "run", capture)
    assert proof.main() == 0
    rehearsal = mode == "--rehearse"
    assert called == [(target, output, rehearsal, None if rehearsal else data_dir / "watchtower.db")]


def test_staged_mock_proof_measures_each_run(tmp_path, monkeypatch):
    db = DB(tmp_path / "watchtower.db")
    monkeypatch.setattr("app.documents.FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(server, "STATE", dict(server.DEFAULT_STATE))
    llm = LLM(db, budget_usd=1, cache_dir=None)

    async def create(**request):
        # The production client wrapper prices and logs this synthetic usage in the
        # test DB. The OpenAI client is never initialized.
        reply = mock_reply(json.loads(request["input"].removeprefix("Answer in JSON.\n")))
        return SimpleNamespace(output_text=json.dumps(reply), usage=SimpleNamespace(input_tokens=20, output_tokens=10))

    llm._client = SimpleNamespace(responses=SimpleNamespace(create=create))

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, llm)
        rows = []
        try:
            await worker.start()
            await asyncio.wait_for(proof.mock_cases(worker, rows.append), timeout=60)
        finally:
            await worker.stop()
            await fetcher.aclose()
        return {row["case"]: row for row in rows}

    cases = asyncio.run(exercise())
    for name in ("mock-ssr", "mock-layout-v2", "mock-captcha"):
        row = cases[name]
        assert row["passed"], row
        assert (row["stored_tenders"], row["documents"]) == (5, 8)
    assert cases["mock-layout-v2"]["transitions"] == ["serving -> healing", "healing -> serving"]
    assert set(cases["mock-layout-v2"]["cost_by_purpose_usd"]) == {"repair"}
    assert cases["mock-captcha"]["transitions"] == ["serving -> failed"]
    assert cases["mock-captcha"]["llm_calls"] == []
    assert sum(row["recorded_cost_usd"] for row in cases.values()) == pytest.approx(db.llm_total_cost())

    # Hosts lacking Chromium can still verify SSR, healing and CAPTCHA above.
    if not cases["mock-spa"]["passed"]:
        from tests.test_js_discover import require_chromium

        asyncio.run(require_chromium())
    assert cases["mock-spa"]["passed"], cases["mock-spa"]
    assert set(proof.real_targets()) == {"etenders-ie", "vergabemarktplatz-nrw-de", "dtvp-de"}


def test_failed_attach_reports_recorded_cost_and_unsettled_reservation(tmp_path, monkeypatch):
    db = DB(tmp_path / "watchtower.db")
    ledger = DB(tmp_path / "shared-budget.db")
    monkeypatch.setattr(server, "STATE", dict(server.DEFAULT_STATE))
    llm = LLM(ledger, budget_usd=1, cache_dir=None)
    replies = iter([
        SimpleNamespace(
            output_text=json.dumps({"list_url": proof.SSR_URL, "detail_url": "/tender/MOCK-001"}),
            usage=SimpleNamespace(input_tokens=20, output_tokens=10),
        ),
        SimpleNamespace(output_text="{}", usage=None),
    ])

    async def create(**request):
        return next(replies)

    llm._client = SimpleNamespace(responses=SimpleNamespace(create=create))

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, llm)
        try:
            await worker.start()
            return await proof.measure(worker, "mock-ssr", db.portal_create(proof.SSR_URL))
        finally:
            await worker.stop()
            await fetcher.aclose()

    row = asyncio.run(exercise())
    assert not row["passed"] and row["state"] == "failed"
    assert row["stored_tenders"] == row["documents"] == 0
    assert row["recorded_cost_usd"] == ledger.llm_total_cost() > 0
    assert db.llm_total_cost() == 0
    assert row["pending_reservations_usd"] > 0
    assert not row["checks"]["all model costs settled"]
