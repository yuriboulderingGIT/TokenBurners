"""End to end through the worker on the mock portal, with the hand-written recipe (no model). OWNER: A2."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from app import documents
from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker
from brain.blocks import Blocked
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


class _NoModel:
    """Stands in for the LLM where a test replaces repair() itself."""

    total_cost = 0.0

    def scoped(self, *_args):
        return self


def _read_once(db: DB, portal_id: str, llm=None) -> None:
    async def run():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.ASGITransport(app=server.app))
        worker = Worker(db, fetcher, llm=llm)  # healthy reads and blocks never call the model
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


@pytest.mark.parametrize("can_repair", [True, False])
def test_document_selector_change_preserves_files_during_repair(portal, monkeypatch, can_repair) -> None:
    db, portal_id = portal
    monkeypatch.setattr(server, "TENDERS", [dict(t, deadline="31 Dec 2099, 09:00") for t in server.TENDERS])
    render = server.render_detail
    link_class = "download"

    def detail(tender_id, layout):
        page = render(tender_id, layout)
        if page is None:
            return None
        # This variant has no generic file marker. Only the recipe identifies the links.
        return re.sub(
            r'<a href="/download/([^/]+)/([^"]+)">[^<]+</a>',
            rf'<a class="{link_class}" href="/dl?tender=\1&amp;file=\2">Open</a>',
            page,
        )

    monkeypatch.setattr(server, "render_detail", detail)
    previous = db.recipe_latest(portal_id)
    previous.documents.link = "//a[@class='download']/@href"
    db.recipe_save(portal_id, previous, "attach", 0)
    _read_once(db, portal_id)
    last_good = db.tenders_for(portal_id)
    assert len(last_good) == 5 and all(t["documents"] for t in last_good)
    link_class = "attachment"

    class RepairModel(_NoModel):
        strong_model_name = "fixture"

        def __init__(self):
            self.requests = []

        async def json(self, prompt, *, system, strong):
            self.requests.append(json.loads(prompt))
            assert db.portal_get(portal_id)["state"] == "healing"
            assert db.tenders_for(portal_id) == last_good
            proposal = previous.model_dump(by_alias=True)
            if can_repair and len(self.requests) == 2:
                proposal["documents"]["link"] = "//a[@class='attachment']/@href"
            return proposal

    model = RepairModel()
    _read_once(db, portal_id, llm=model)
    assert "document links disappeared for 5 of 5" in model.requests[0]["feedback"][0]
    assert "sampled tenders still have no document links" in model.requests[1]["feedback"]
    assert db.tenders_for(portal_id) == last_good
    [repair_run] = [r for r in db.runs_for(portal_id) if r["kind"] == "repair"]
    if can_repair:
        assert len(model.requests) == 2
        assert db.portal_get(portal_id)["state"] == "serving"
        assert repair_run["result"] == "ok"
    else:
        assert len(model.requests) == 2  # the same rejected repair twice exhausts the retry
        assert db.portal_get(portal_id)["state"] == "failed"
        assert repair_run["result"] == "failed"
        assert db.recipe_latest(portal_id).version == previous.version


def test_documents_behind_a_login_fail_the_portal_with_a_reason(portal) -> None:
    db, portal_id = portal
    server.STATE["docs"] = "login"
    _read_once(db, portal_id)
    row = db.portal_get(portal_id)
    assert row["state"] == "failed"
    assert "login required" in row["reason"]
    assert db.tenders_for(portal_id) == []


def test_failed_file_revalidation_keeps_the_verified_file(portal, monkeypatch):
    db, portal_id = portal
    _read_once(db, portal_id)
    previous = db.tenders_for(portal_id)
    respond = server._file_response

    def fail_one(tender_id, filename):
        if tender_id == "MOCK-001":
            raise server.HTTPException(503)
        return respond(tender_id, filename)

    monkeypatch.setattr(server, "_file_response", fail_one)
    db.execute("UPDATE document_sources SET last_downloaded_at=0")
    _read_once(db, portal_id)
    # One file server outage must not stop the read: the tender keeps its last verified file.
    assert db.tenders_for(portal_id) == previous
    assert db.portal_get(portal_id)["state"] == "serving"
    notes = " ".join(run["notes"] for run in db.runs_for(portal_id))
    assert "1 known document(s) could not be checked again" in notes
    assert db.one("SELECT count(DISTINCT run_id) AS n FROM read_documents")["n"] == 2
    assert all(run["kind"] == "read" for run in db.runs_for(portal_id))


def test_file_over_the_cap_is_left_out_and_the_portal_still_serves(portal, monkeypatch) -> None:
    db, portal_id = portal
    monkeypatch.setattr(documents, "MAX_DOC_BYTES", 10_000)
    monkeypatch.setattr(server, "BIG_FILE_BYTES", 50_000)  # stands in for 300 MB in a fast test
    server.STATE["docs"] = "big"
    _read_once(db, portal_id)
    assert db.portal_get(portal_id)["state"] == "serving"
    big = next(t for t in db.tenders_for(portal_id) if t["id"] == server.BIG_TENDER)
    assert [d["fileName"] for d in big["documents"]] == ["tender-documents.pdf"]


def test_block_met_during_repair_reports_blocked_and_finishes_the_repair_run(portal, monkeypatch) -> None:
    db, portal_id = portal
    _read_once(db, portal_id)
    assert db.portal_get(portal_id)["state"] == "serving"

    async def blocked_repair(*_args, **_kwargs):
        raise Blocked("captcha")

    monkeypatch.setattr("app.worker.repair", blocked_repair)
    server.STATE["layout"] = "v2"  # recipe v1 finds nothing -> health fails -> repair
    _read_once(db, portal_id, llm=_NoModel())
    row = db.portal_get(portal_id)
    assert row["state"] == "failed"
    assert row["reason"] == "blocked: captcha required"
    [repair_run] = [r for r in db.runs_for(portal_id) if r["kind"] == "repair"]
    assert repair_run["result"] == "failed" and repair_run["finished_at"]
    assert len(db.tenders_for(portal_id)) == 5  # the last good tenders are kept


def test_captcha_portal_fails_with_a_reason(portal) -> None:
    db, portal_id = portal
    server.STATE["variant"] = "captcha"
    _read_once(db, portal_id)
    row = db.portal_get(portal_id)
    assert row["state"] == "failed"
    assert "captcha" in row["reason"]
