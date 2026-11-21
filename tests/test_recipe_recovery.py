"""Verify structural failures never replace the worker's last healthy listing."""

import asyncio
import json

import httpx
import pytest

from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker

_BASE = "https://portal.example"
_ENTRY = _BASE + "/start"
_OLD_LIST = _BASE + "/results"
_NEW_LIST = _BASE + "/notices"


@pytest.mark.parametrize(("kind", "fault", "problem", "repairable"), [
    ("html", "pager", "missed the advertised next page", True),
    ("json_api", "total", "pagination exposed only 1 entries", True),
    ("html", "missing", "HTTP 404", True),
    ("json_api", "missing", "HTTP 404", True),
    ("json_api", "bad-format", "could not parse portal response as json_api", True),
    ("html", "bad-detail", "detail.link found no URL", True),
    ("html", "unavailable", "HTTP 503", False),
    ("html", "login", "login required", False),
])
def test_worker_keeps_previous_tenders_and_repairs_only_recipe_failures(
    tmp_path, monkeypatch, kind, fault, problem, repairable,
) -> None:
    recipe = Recipe.model_validate({
        "kind": kind,
        "list": {
            "url": _OLD_LIST,
            "item": "$.items[*]" if kind == "json_api" else "//li[@data-id]",
            "next_page": "$.next" if kind == "json_api" else "//a[@rel='next']/@href",
        },
        "fields": (
            {"id": "$.id", "title": "$.title"} if kind == "json_api"
            else {"id": "@data-id", "title": "text()"}
        ),
        # Every tender has a document, so a healthy read is a correct delivery (serving).
        "documents": {"link": "$.doc" if kind == "json_api" else "./a[@class='doc']/@href"},
    })
    db = DB(tmp_path / "recovery.sqlite")
    portal_id = db.portal_create(_ENTRY)
    db.recipe_save(portal_id, recipe, "attach", 0.0)
    changed = False
    baseline = []
    repaired = []

    class FixtureLLM:
        total_cost = 0.0

        def scoped(self, *_args):
            return self

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/files/"):
            return httpx.Response(200, content=b"%PDF-1.4 tender", headers={"content-type": "application/pdf"})
        old = request.url.path == "/results"
        if old and changed:
            if fault == "missing":
                return httpx.Response(404)
            if fault == "bad-format":
                return httpx.Response(200, text="<h1>The API moved</h1>")
            if fault == "unavailable":
                return httpx.Response(503)
            if fault == "login":
                return httpx.Response(401)
        second = request.url.params.get("page") == "2"
        ident = "B" if second else "A"
        if kind == "json_api":
            return httpx.Response(200, text=json.dumps({
                "items": [{"id": ident, "title": f"Tender {ident}", "doc": f"/files/{ident}.pdf"}],
                "total_count": 2,
                "next": None if second or (old and changed and fault == "total") else "?page=2",
            }))
        page = f'<ul><li data-id="{ident}">Tender {ident}<a class="doc" href="/files/{ident}.pdf">PDF</a></li></ul>'
        if not second:
            page += (
                '<a href="?page=2">Weiter</a>' if old and changed and fault == "pager"
                else '<a href="?page=2" rel="next">Next page</a>'
            )
        return httpx.Response(200, text=page)

    async def fake_repair(old, health, _fetch, _llm, *, portal_url, pagination_page, stored_tenders):
        assert problem in health.problems[0]
        assert portal_url == _ENTRY
        assert old.list_.url == _OLD_LIST
        assert db.portal_get(portal_id)["state"] == "healing"
        assert db.tenders_for(portal_id) == baseline
        assert [tender.id for tender in stored_tenders] == ["A", "B"]
        if fault == "pager":
            assert pagination_page["url"] == _OLD_LIST
        repaired.append(True)
        return recipe.model_copy(update={"list_": recipe.list_.model_copy(update={"url": _NEW_LIST})})

    monkeypatch.setattr("app.worker.repair", fake_repair)

    async def exercise():
        nonlocal changed
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(respond))
        worker = Worker(db, fetcher, FixtureLLM())
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            baseline.extend(db.tenders_for(portal_id))
            assert [(row["id"], row["status"]) for row in baseline] == [("A", "open"), ("B", "open")]

            changed = True
            if fault == "bad-detail":
                broken = recipe.model_copy(deep=True)
                broken.detail.link = ".//a[@class='detail']/@href"
                db.recipe_save(portal_id, broken, "attach", 0)
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
            assert bool(repaired) == repairable
            assert [row["status"] for row in db.tenders_for(portal_id)] == ["open", "open"]
            row = db.portal_get(portal_id)
            if repairable:
                assert row["state"] == "serving"
                assert db.recipe_latest(portal_id).list_.url == _NEW_LIST
            else:
                assert db.tenders_for(portal_id) == baseline
                assert row["state"] == ("failed" if fault == "login" else "serving")
                assert problem in row["reason"]
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(exercise())
