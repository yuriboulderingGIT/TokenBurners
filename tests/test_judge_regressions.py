"""Exercise the judge's published portal shapes and the failures reported on first reads."""

import asyncio
import hashlib
import json
import zipfile
import zlib
from datetime import datetime, timezone

import httpx
import pytest
from lxml import html

from app import main
from app.db import DB
from app.documents import _zip_name
from app.fetcher import FakeFetcher, _filename
from app.health import check_health
from app.models import Locale, RawTender, ReadResult
from app.worker import Worker
from brain.learn import AttachFailed, learn_recipe
from brain.normalize import normalize, shows_deadline_time
from brain.run_recipe import _numbered_pager
from judge import fixture
from mock_portal import server


class ListingOnlyModel:
    """The weak proposal seen by the judges: a link used as the ID and no pagination or files."""

    model_name = strong_model_name = "offline"
    total_cost = 0

    def scoped(self, *_args):
        return self

    async def json(self, prompt, **_kwargs):
        evidence = json.loads(prompt)
        if "entry" in evidence:
            return {"list_url": evidence["entry"]["url"], "detail_url": None}
        return {"list": {"url": evidence["list_page"]["url"], "item": "//tr[td]"},
                "fields": {"id": "./td[2]/a/@href", "title": "./td[2]/a"}}


class InvalidRecipeModel(ListingOnlyModel):
    async def json(self, prompt, **kwargs):
        if "entry" in json.loads(prompt):
            return await super().json(prompt, **kwargs)
        return {"list": {"url": "/unobserved", "item": "//tr[td]"}}


@pytest.mark.parametrize(("easy", "model", "total"), [
    (False, ListingOnlyModel, False), (True, ListingOnlyModel, False),
    (False, InvalidRecipeModel, False), (False, ListingOnlyModel, True),
], ids=["hard", "easy", "invalid-proposal", "numbered-with-total"])
def test_judge_portal_url_to_verified_file_bytes(tmp_path, monkeypatch, easy, model, total):
    base = "http://localhost:8092"
    listing = base + "/ausschreibungen"
    monkeypatch.setattr(fixture, "STATE", {"easy": easy, "captcha": False, "v2": False})
    count = "<script>const totalResults=7; if (offset < totalResults) {}</script>" if total else ""
    pages = {listing: fixture.listing(1).replace("</body>", count + "</body>")}
    for n in range(1, 4):
        pages[f"{listing}?seite={n}"] = fixture.listing(n).replace("</body>", count + "</body>")
    for record in fixture.T:
        tid = record[0]
        pages[f"{base}/notice/{tid}"] = fixture.summary(record)
        pages[f"{base}/vergabe/{tid}"] = fixture.detail(record)
        for name, body in fixture.files(tid).items():
            pages[f"{base}/dl/{tid}/{name}"] = body
    database = DB(tmp_path / "judge.db")
    monkeypatch.setattr(main, "db", database)
    fetcher = FakeFetcher(pages)
    worker = Worker(database, fetcher, model())
    monkeypatch.setattr(main.app.state, "worker", worker, raising=False)

    async def exercise():
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://api") as client:
                registered = await client.post("/portals", json={"url": listing})
                assert registered.status_code == 201
                path = "/portals/" + registered.json()["id"]
                await worker._handle(*worker.queue.get_nowait())
                portal = (await client.get(path)).json()
                assert portal["state"] == "serving", portal["reason"]
                rows = (await client.get(path + "/tenders")).json()
                (tmp_path / "delivered.json").write_text(json.dumps(rows), encoding="utf-8")
                by_id = {row["id"]: row for row in rows}
                assert len(by_id) == len(rows) == len(fixture.T)
                for tid, ref, title, buyer, pub, deadline, cpv, nature in fixture.T:
                    row = by_id[tid]
                    assert (row["referenceNumber"], row["title"], row["buyerName"]) == (ref, title, buyer)
                    assert row["url"] == f"{base}/vergabe/{tid}"
                    assert row["publicationDate"] == datetime.strptime(pub, "%d.%m.%Y").date().isoformat()
                    due = datetime.strptime(deadline + " +0100", "%d.%m.%Y %H:%M %z")
                    assert row["submissionDeadline"] == due.isoformat()
                    assert row["status"] == ("closed" if due <= datetime.now(timezone.utc) else "open")
                    assert row["cpvCodes"] == [cpv.partition("-")[0]] and row["procedureType"] == "open"
                    assert row["contractNature"] == {"Bauleistung": "works", "Dienstleistung": "services",
                                                      "Lieferleistung": "supplies"}[nature]
                    assert row["placeOfPerformance"] == {"postalCode": "12345", "city": "Hohenfeld", "countryCode": "DE"}
                    expected = {fixture.pdf(tid + suffix) for suffix in ("-BM", "-LV", "-AS")}
                    actual = set()
                    for doc in row["documents"]:
                        file = await client.get(doc["path"])
                        assert file.status_code == 200
                        assert hashlib.sha256(file.content).hexdigest() == doc["sha256"]
                        assert len(file.content) == doc["sizeBytes"]
                        actual.add(file.content)
                    assert len(row["documents"]) == 3 and actual == expected
                [run] = database.runs_for(portal["id"])
                assert run["request_count"] == len(fetcher.request_log)
                detail = html.fromstring((await client.get("/ui" + path)).text)
                history = detail.get_element_by_id("tab-runs").text_content()
                assert f"{run['request_count']} requests" in history and "$0.0000" in history and "took" in history
                old = database.portal_create("http://old.example/closed-feed")
                database.portal_update(old, state="failed", tender_count=3, last_success_at=portal["lastSuccessAt"])
                dashboard = html.fromstring((await client.get("/")).text)
                [kpi] = dashboard.xpath("//div[div[@class='label' and normalize-space()='Tenders delivered']]")
                assert kpi.xpath("string(./div[@class='value'])") == "7"
                assert "from 1 serving portal" in kpi.text_content()
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_deadline_without_zone_stays_unknown_and_closed_source_status_wins():
    raw = RawTender(url="https://portal.example/notice/1", fields={
        "id": "1", "title": "Roads", "status": "abgelaufen", "submissionDeadline": "09 Dec 2026, 10:00",
    })
    tender = normalize(raw, Locale())
    assert tender.status == "closed" and tender.submissionDeadline is None
    assert not check_health(None, ReadResult(tenders=[tender], deadline_times_seen=int(shows_deadline_time(raw)))).ok
    raw.source_language = "en-IE"
    assert normalize(raw, Locale()).submissionDeadline == "2026-12-09T10:00:00+00:00"


def test_two_page_numbered_pager_preserves_the_filter_and_requires_local_evidence():
    base = "https://buyer.example/notices?buyer=city"
    url = base + "&page=2"
    page = html.fromstring('<nav><span aria-current="page">1</span><a href="?buyer=city&amp;page=2">2</a></nav>')
    assert _numbered_pager(page, base) == (1, {2: url})
    assert _numbered_pager(page, "https://buyer.example/notices?buyer=state") is None
    other = html.fromstring('<section><nav><span aria-current="page">1</span></nav>'
                            '<article><a href="?buyer=city&amp;page=2">2</a></article></section>')
    assert _numbered_pager(other, base) is None


def test_identical_failed_attach_proposals_stop_before_a_strong_model_call(monkeypatch):
    base = "https://buyer.example/tenders"
    attempts = []

    class RepeatedInvalidModel(InvalidRecipeModel):
        async def json(self, prompt, **kwargs):
            if "list_page" in json.loads(prompt):
                attempts.append(kwargs.get("strong", False))
            return await super().json(prompt, **kwargs)

    async def no_feed(*args, **kwargs):
        return None

    monkeypatch.setattr("brain.js_discover.discover_json_api", no_feed)

    async def exercise():
        fetcher = FakeFetcher({base: "<h1>Public tenders</h1><article>Road repairs</article>"})
        try:
            with pytest.raises(AttachFailed):
                await learn_recipe(base, fetcher.for_portal("test"), RepeatedInvalidModel())
            assert attempts == [False, False]
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_team_mock_recovers_a_path_id_and_uses_the_declared_locale():
    base = "https://buyer.example"
    listing = base + "/tenders"
    pages = {listing: server.render_list(1), listing + "?page=2": server.render_list(2)}
    for tender in server.TENDERS:
        pages[f"{base}/tender/{tender['id']}"] = server.render_detail(tender["id"])
    class WeakMockModel(ListingOnlyModel):
        async def json(self, prompt, **kwargs):
            if "entry" in json.loads(prompt):
                return await super().json(prompt, **kwargs)
            return {"list": {"url": listing, "item": "//tr[td]", "next_page": "//a[@rel='next']/@href"},
                    "fields": {"id": "./td[1]/a/@href", "title": "./td[1]/a"}}

    async def exercise():
        from brain.run_recipe import run_recipe
        fetcher = FakeFetcher(pages)
        try:
            fetch = fetcher.for_portal("test")
            recipe = await learn_recipe(listing, fetch, WeakMockModel())
            rows = await run_recipe(recipe, fetch)
            assert [r.fields["id"] for r in rows] == [r["id"] for r in server.TENDERS]
            assert all(normalize(r, recipe.locale).submissionDeadline and r.document_links for r in rows)
        finally:
            await fetcher.aclose()
    asyncio.run(exercise())


def test_names_use_header_encoding_and_archive_evidence():
    header = 'attachment; filename="Formul%C3%A1%C5%99%20nab%C3%ADdky.docx"'
    assert _filename(httpx.Response(200, headers={"Content-Disposition": header})) == "Formulář nabídky.docx"
    extended = "attachment; filename=fallback.pdf; filename*=utf-8''caf%C3%A9.pdf"
    assert _filename(httpx.Response(200, headers={"Content-Disposition": extended})) == "café.pdf"
    name = "severní_křídlo_konstrukční.pdf"
    raw = name.encode("cp852")
    info = zipfile.ZipInfo(raw.decode("cp437"))
    assert _zip_name(info, "cz") == name
    payload = bytes([1]) + zlib.crc32(raw).to_bytes(4, "little") + name.encode("utf-8")
    info.extra = b"up" + len(payload).to_bytes(2, "little") + payload
    assert _zip_name(info, "") == name
    fallback = zipfile.ZipInfo("attachment.pdf")
    payload = bytes([1]) + zlib.crc32(b"attachment.pdf").to_bytes(4, "little") + name.encode("utf-8")
    fallback.extra = b"up" + len(payload).to_bytes(2, "little") + payload
    assert _zip_name(fallback, "") == name


def test_upgrade_archives_only_rows_published_by_a_failed_first_read(tmp_path):
    path = tmp_path / "legacy.db"
    original = DB(path)
    ids = [original.portal_create(f"https://example.org/{n}") for n in range(3)]
    item = normalize(RawTender(url="https://example.org/road", fields={"id": "N1", "title": "Road"}), Locale())
    for pid in ids:
        original.save_tenders(pid, [item])
    for pid in ids[:2]:
        run = original.run_create(pid, "read")
        original.run_finish(run, "failed", "read ok, but no tender documents were found")
    original.portal_update(ids[1], last_success_at="2026-11-20T10:00:00+00:00")

    for _ in range(2):
        upgraded = DB(path)
        assert upgraded.tenders_for(ids[0]) == []
        assert upgraded.portal_get(ids[0])["tender_count"] == 0
        assert [row["portal_id"] for row in upgraded.all("SELECT portal_id FROM unverified_tenders")] == [ids[0]]
        assert all(upgraded.tenders_for(pid)[0]["id"] == "N1" for pid in ids[1:])
