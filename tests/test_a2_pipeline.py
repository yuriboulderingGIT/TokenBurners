"""End to end through the worker on the mock portal, with the hand-written recipe (no model). OWNER: A2."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import documents
from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker
from mock_portal import server

PORTAL_URL = "http://portal.example/tenders"
RECIPE = Path(__file__).parents[1] / "testset/fixtures/mock/recipe_v1.json"


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(server, "STATE", dict(server.DEFAULT_STATE))
    db = DB(tmp_path / "a2.sqlite")
    portal_id = db.portal_create(PORTAL_URL)
    recipe = Recipe.model_validate(json.loads(RECIPE.read_text(encoding="utf-8")))
    recipe.list_.url = PORTAL_URL
    db.recipe_save(portal_id, recipe, "attach", 0)
    return db, portal_id


def _read_once(db: DB, portal_id: str) -> None:
    async def run():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, llm=None)  # healthy reads and blocks never call the model
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=10)
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(run())


def test_documents_are_downloaded_unpacked_and_served(portal) -> None:
    db, portal_id = portal
    _read_once(db, portal_id)
    assert db.portal_get(portal_id)["state"] == "serving"
    tenders = {t["id"]: t for t in db.tenders_for(portal_id)}
    assert len(tenders) == 5
    assert [d["fileName"] for d in tenders["MOCK-001"]["documents"]] == ["tender-documents.pdf"]
    assert {d["fileName"] for d in tenders["MOCK-003"]["documents"]} == {"tender-documents.pdf"} | server.PACKAGE_FILES
    for tender in tenders.values():
        for doc in tender["documents"]:
            row = db.document_get(doc["path"].removeprefix("/files/"))
            assert Path(row["path_on_disk"]).stat().st_size == doc["sizeBytes"]

    # A refresh reuses the files: same paths, and the tenders don't count as changed.
    changed_at = {t["id"]: t["lastChangedAt"] for t in tenders.values()}
    _read_once(db, portal_id)
    again = {t["id"]: t for t in db.tenders_for(portal_id)}
    assert {i: t["documents"] for i, t in again.items()} == {i: t["documents"] for i, t in tenders.items()}
    assert {i: t["lastChangedAt"] for i, t in again.items()} == changed_at


def test_documents_behind_a_login_fail_the_portal_with_a_reason(portal) -> None:
    db, portal_id = portal
    server.STATE["docs"] = "login"
    _read_once(db, portal_id)
    row = db.portal_get(portal_id)
    assert row["state"] == "failed"
    assert "login required" in row["reason"]
    assert db.tenders_for(portal_id) == []


def test_file_over_the_cap_is_left_out_and_the_portal_still_serves(portal, monkeypatch) -> None:
    db, portal_id = portal
    monkeypatch.setattr(documents, "MAX_DOC_BYTES", 10_000)
    monkeypatch.setattr(server, "BIG_FILE_BYTES", 50_000)  # stands in for 300 MB in a fast test
    server.STATE["docs"] = "big"
    _read_once(db, portal_id)
    assert db.portal_get(portal_id)["state"] == "serving"
    big = next(t for t in db.tenders_for(portal_id) if t["id"] == server.BIG_TENDER)
    assert [d["fileName"] for d in big["documents"]] == ["tender-documents.pdf"]


def test_captcha_portal_fails_with_a_reason(portal) -> None:
    db, portal_id = portal
    server.STATE["variant"] = "captcha"
    _read_once(db, portal_id)
    row = db.portal_get(portal_id)
    assert row["state"] == "failed"
    assert "captcha" in row["reason"]
