"""Offline read-path regressions for failures observed in real portal attempts."""

import asyncio
import json

import httpx
import pytest

from app.db import DB
from app.fetcher import FakeFetcher, Fetcher
from app.health import check_health
from app.models import ReadResult, Recipe
from app.worker import Worker
from brain.normalize import normalize, shows_deadline_time
from brain.run_recipe import run_recipe


BASE = "https://portal.example"
LIST = BASE + "/notices"


def _read(recipe: dict, pages: dict[str, str | bytes]):
    async def exercise():
        fetcher = FakeFetcher(pages)
        try:
            parsed = Recipe.model_validate(recipe)
            return parsed, await run_recipe(parsed, fetcher.for_portal("diagnostic"))
        finally:
            await fetcher.aclose()

    return asyncio.run(exercise())


def test_complete_json_notice_feed_is_a_healthy_control():
    items = [{"id": f"N{i}", "title": f"Contract {i}", "buyer": "City",
              "deadline": "2026-12-15T12:00:00+01:00", "url": f"/notice/{i}"}
             for i in range(1, 6)]
    recipe = {"kind": "json_api", "list": {"url": LIST, "item": "$.items[*]"},
              "fields": {"id": "$.id", "title": "$.title", "buyerName": "$.buyer",
                         "submissionDeadline": "$.deadline", "url": "$.url"}}
    parsed, raw = _read(recipe, {LIST: json.dumps({"items": items, "total": 5})})
    tenders = [normalize(row, parsed.locale) for row in raw]
    assert len(raw) == 5 and all(tenders)
    assert check_health(None, ReadResult(tenders=tenders, raw_count=len(raw))).ok


def test_stylesheet_text_in_buyer_cannot_verify_a_read():
    # achatpublic.com was reported serving with up to 63,367 CSS characters as buyerName.
    css = "Retour .sdmCardConsult__bloc { display: grid; } " * 1300
    recipe = {
        "list": {"url": LIST, "item": "//article"},
        "fields": {
            "id": "./@data-id", "title": "./h2/a", "buyerName": "./span[@class='buyer']",
            "submissionDeadline": "./time", "url": "./h2/a/@href",
        },
        "locale": {"country": "FR"},
        "documents": {"link": "./a[@class='file']/@href"},
    }
    rows = "".join(
        f'<article data-id="N{i}"><h2><a href="/notice/{i}">Contract {i}</a></h2>'
        f'<span class="buyer">{css}</span><time>15/12/2026 à 12h00</time>'
        f'<a class="file" href="/files/{i}.pdf">PDF</a></article>'
        for i in range(1, 4)
    )
    parsed, raw = _read(recipe, {LIST: f"<main>{rows}</main>"})
    tenders = [normalize(row, parsed.locale) for row in raw]
    assert len(raw) == 3 and all(tenders)
    assert all(len(t.buyerName) > 500 for t in tenders)
    result = ReadResult(tenders=[t for t in tenders if t], raw_count=len(raw),
                        deadline_times_seen=sum(shows_deadline_time(row) for row in raw))
    assert not check_health(None, result).ok


def test_one_unidentified_notice_makes_coverage_unverified():
    # A first read must account for every selected notice row before claiming full coverage.
    items = [
        {"id": f"N{i}" if i != 5 else None, "title": f"Contract {i}",
         "buyer": "City", "deadline": "2026-12-15T12:00:00+01:00",
         "url": f"/notice/{i}"}
        for i in range(1, 6)
    ]
    recipe = {
        "kind": "json_api", "list": {"url": LIST, "item": "$.items[*]"},
        "fields": {"id": "$.id", "title": "$.title", "buyerName": "$.buyer",
                   "submissionDeadline": "$.deadline", "url": "$.url"},
    }
    parsed, raw = _read(recipe, {LIST: json.dumps({"items": items, "total": 5})})
    tenders = [normalize(row, parsed.locale) for row in raw]
    assert len(raw) == 5 and sum(t is not None for t in tenders) == 4
    result = ReadResult(tenders=[t for t in tenders if t], raw_count=len(raw))
    assert not check_health(None, result).ok


def test_json_listing_can_open_html_notice_details():
    # Common aggregator shape: JSON search result, HTML detail page, list-only buyer.
    recipe = {
        "kind": "json_api", "list": {"url": LIST, "item": "$.items[*]"},
        "detail": {"link": "$.href"},
        "fields": {"id": "$.id", "title": "$.title", "buyerName": "$.buyer"},
    }
    pages = {
        LIST: json.dumps({"items": [{"id": "N1", "title": "Bridge repair",
                                    "buyer": "City council", "href": "/notice/1"}]}),
        BASE + "/notice/1": "<html><main><h1>Bridge repair</h1><p>Public notice</p></main></html>",
    }
    parsed, raw = _read(recipe, pages)
    assert len(raw) == 1
    assert normalize(raw[0], parsed.locale).buyerName == "City council"


def test_authoritative_empty_feed_is_a_verified_empty_read():
    recipe = {"kind": "json_api", "list": {"url": LIST, "item": "$.items[*]"},
              "fields": {"id": "$.id", "title": "$.title"}}
    parsed, raw = _read(recipe, {LIST: '{"items":[],"total":0}'})
    assert raw == []
    result = ReadResult(tenders=[], raw_count=0, verified_empty=raw.verified_empty)
    assert raw.verified_empty and check_health(None, result).ok


def _worker_recipe():
    return Recipe.model_validate({
        "list": {"url": LIST, "item": "//article"},
        "fields": {"id": "./@data-id", "title": "./h2/a", "buyerName": "./span[@class='buyer']",
                   "submissionDeadline": "./time", "status": "./@data-status"},
        "documents": {"link": "./a[@class='file']/@href"},
        "locale": {"country": "FR"},
    })


def _listing_with_files(statuses: list[str]):
    return "<main>" + "".join(
        f'<article data-id="N{i}" data-status="{status}"><h2><a href="/notice/{i}">'
        f'Contract {i}</a></h2><span class="buyer">City council</span>'
        f'<time>15/12/2026 à 12h00</time><a class="file" href="/files/{i}.pdf">PDF</a></article>'
        for i, status in enumerate(statuses, 1)
    ) + "</main>"


def test_login_wall_on_known_documents_is_detected_on_next_read(tmp_path):
    state = {"login": False, "file_requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notices":
            return httpx.Response(200, text=_listing_with_files(["open"]))
        if request.url.path == "/files/1.pdf":
            state["file_requests"] += 1
            if state["login"]:
                return httpx.Response(200, text="<html><h1>Login required</h1><form><input type=password></form></html>",
                                      headers={"content-type": "text/html"})
            return httpx.Response(200, content=b"%PDF-1.4\nfixture\n%%EOF", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def exercise():
        db = DB(tmp_path / "login-check.db")
        portal_id = db.portal_create(LIST)
        db.recipe_save(portal_id, _worker_recipe(), "fixture", 0)
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        worker = Worker(db, fetcher, llm=None)
        try:
            first = db.run_create(portal_id, "read")
            assert await worker._read(portal_id, first, allow_repair=False)
            assert db.portal_get(portal_id)["state"] == "serving"
            assert state["file_requests"] == 1
            state["login"] = True
            second = db.run_create(portal_id, "read")
            await worker._read(portal_id, second, allow_repair=False)
            assert state["file_requests"] >= 2
            assert db.portal_get(portal_id)["state"] == "failed"
            assert "login" in (db.portal_get(portal_id)["reason"] or "").lower()
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_document_login_on_initial_read_is_detected(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notices":
            return httpx.Response(200, text=_listing_with_files(["open"]))
        if request.url.path == "/files/1.pdf":
            return httpx.Response(200, text="<html><h1>Login required</h1><form><input type=password></form></html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def exercise():
        db = DB(tmp_path / "initial-login.db")
        portal_id = db.portal_create(LIST)
        db.recipe_save(portal_id, _worker_recipe(), "fixture", 0)
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        worker = Worker(db, fetcher, llm=None)
        try:
            run_id = db.run_create(portal_id, "read")
            assert not await worker._read(portal_id, run_id, allow_repair=False)
            portal = db.portal_get(portal_id)
            assert portal["state"] == "failed"
            assert "login" in (portal["reason"] or "").lower()
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_html_fragment_cannot_be_published_as_a_pdf(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notices":
            return httpx.Response(200, text=_listing_with_files(["open"]))
        if request.url.path == "/files/1.pdf":
            return httpx.Response(200, text="<h1>Login required</h1><form><input type=password></form>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def exercise():
        db = DB(tmp_path / "fragment-login.db")
        portal_id = db.portal_create(LIST)
        db.recipe_save(portal_id, _worker_recipe(), "fixture", 0)
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        worker = Worker(db, fetcher, llm=None)
        try:
            run_id = db.run_create(portal_id, "read")
            await worker._read(portal_id, run_id, allow_repair=False)
            assert db.portal_get(portal_id)["state"] == "failed"
            assert db.tenders_for(portal_id) == []
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_first_read_skips_files_for_explicitly_closed_archive(tmp_path):
    fetched = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notices":
            return httpx.Response(200, text=_listing_with_files(["open", "closed", "closed", "closed"]))
        if request.url.path.startswith("/files/"):
            fetched.append(request.url.path)
            return httpx.Response(200, content=b"%PDF-1.4\nfixture\n%%EOF",
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def exercise():
        db = DB(tmp_path / "closed-archive.db")
        portal_id = db.portal_create(LIST)
        db.recipe_save(portal_id, _worker_recipe(), "fixture", 0)
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        worker = Worker(db, fetcher, llm=None)
        try:
            run_id = db.run_create(portal_id, "read")
            assert await worker._read(portal_id, run_id, allow_repair=False)
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())
    assert fetched == ["/files/1.pdf"]
