"""Regression coverage for a localized route, a late open notice and changed file access."""
import asyncio
import hashlib
import json

import httpx
import pytest
from lxml import html

from app import main
from app.db import DB
from app.fetcher import FakeFetcher, RunPageCache
from app.health import check_health, clean_short_fields
from app.models import Locale, RawTender, ReadResult, Recipe
from app.worker import Worker
from brain.normalize import normalize
from brain.run_recipe import RecipeReadError, _document_panel, run_recipe
from brain.scope import archive_open_view


class RecordedProposal:
    total_cost = 0
    model_name = strong_model_name = "replay"

    def __init__(self, url, recipe):
        self.url = url
        self.recipe = recipe
        self.calls = 0
        self.saw_index = False

    def scoped(self, *_args):
        return self

    async def json(self, prompt, **kwargs):
        self.calls += 1
        evidence = json.loads(prompt)
        if "entry" in evidence:
            return {"list_url": self.url, "detail_url": self.url + "n/1"}
        self.saw_index |= bool(evidence.get("attachment_index"))
        return self.recipe


@pytest.mark.parametrize("lang,zone,deadline,fmt,offset,labels", [
    ("el-GR", "Europe/Athens", "09/12/2099 στις 10:45", "%d/%m/%Y στις %H:%M", "+02:00", ("Ανοιχτή", "Κλειστή", "Αποσύρθηκε")),
    ("ja-JP", "Asia/Tokyo", "2099年12月09日 10時45分", "%Y年%m月%d日 %H時%M分", "+09:00", ("公開中", "受付終了", "取下げ")),
    ("ar-EG", "Africa/Cairo", "٠٩/١٢/٢٠٩٩ الساعة ١٠:٤٥", "%d/%m/%Y الساعة %H:%M", "+02:00", ("مفتوح", "مغلق", "مسحوب")),
])
def test_url_to_files_and_next_refresh(tmp_path, monkeypatch, lang, zone, deadline, fmt, offset, labels):
    root = "https://buyer.example"
    url = root + "/"
    def page(body):
        return f'<html lang="{lang}"><main>{body}</main></html>'
    pages = {url: page("".join(f'<div><h2><a href="/n/{i}">Ω {i}</a></h2></div>' for i in range(1, 4)))}
    for i, label in enumerate(labels, 1):
        pages[f"{root}/n/{i}"] = page(
            f'<h1>Ω {i}</h1><dl><dd id="number">R-{i}</dd>'
            f'<dd id="buyer"><style>.a{{color:red}}</style>Δήμος <span hidden>menu</span>市</dd>'
            f'<dd id="date">{deadline}</dd><dd id="state">{label}</dd></dl><a href="/a/{i}">📎</a>')
        pages[f"{root}/a/{i}"] = page(f'<a download href="/blob/{i}">PDF {i}</a>')
        pages[f"{root}/blob/{i}"] = f"%PDF-1.4 tender {i}\n%%EOF".encode()
    recipe = {"list": {"url": url, "item": "//div[h2/a]"}, "detail": {"link": "./h2/a/@href"},
              "fields": {"id": "//*[@id='number']", "title": "//h1", "buyerName": "//*[@id='buyer']",
                         "status": "//*[@id='state']", "submissionDeadline": "//*[@id='date']"},
              "documents": {"page": "//main/a/@href", "link": "//a[@download]/@href"},
              "locale": {"lang": lang, "country": lang[-2:], "tz": zone, "deadline_format": fmt,
                         "status_labels": dict(zip(labels, ("open", "closed", "withdrawn"), strict=True))}}
    model = RecordedProposal(url, recipe)
    db = DB(tmp_path / "delivery.db")
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr("app.documents.FILES_DIR", tmp_path / "files")
    fetcher = FakeFetcher(pages)
    worker = Worker(db, fetcher, model)
    monkeypatch.setattr(main.app.state, "worker", worker, raising=False)

    async def run():
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://api") as api:
                pid = (await api.post("/portals", json={"url": url})).json()["id"]
                path = "/portals/" + pid
                await worker._handle(*worker.queue.get_nowait())
                portal = (await api.get(path)).json()
                assert portal["state"] == "serving", portal["reason"]
                assert model.saw_index
                rows = (await api.get(path + "/tenders")).json()
                assert len(rows) == 3
                for row in rows:
                    i = int(row["id"].split("-")[1])
                    assert row["url"] == f"{root}/n/{i}"
                    assert row["status"] == ("open", "closed", "withdrawn")[i - 1]
                    assert row["buyerName"] == "Δήμος 市"
                    assert row["submissionDeadline"] == "2099-12-09T10:45:00" + offset
                    doc, = row["documents"]
                    content = (await api.get(doc["path"])).content
                    assert content == pages[f"{root}/blob/{i}"]
                    assert hashlib.sha256(content).hexdigest() == doc["sha256"]
                attach_calls = model.calls
                probes = []
                for _ in range(2):
                    before = len(fetcher.request_log)
                    worker.request_read(pid)
                    await worker._handle(*worker.queue.get_nowait())
                    assert db.portal_get(pid)["state"] == "serving"
                    checked = [u for _, _, u in list(fetcher.request_log)[before:] if "/blob/" in u]
                    assert len(checked) == 1
                    probes.extend(checked)
                assert len(set(probes)) == 2 and model.calls == attach_calls
                for i in range(1, 4):
                    pages[f"{root}/blob/{i}"] = '<html><h1>Sign in</h1><form><input type="password"></form></html>'
                worker.request_read(pid)
                await worker._handle(*worker.queue.get_nowait())
                assert db.portal_get(pid)["state"] == "failed"
                assert "login" in db.portal_get(pid)["reason"].lower()
                assert (await api.get(path + "/tenders")).json() == rows
                for row in rows:
                    doc, = row["documents"]
                    assert hashlib.sha256((await api.get(doc["path"])).content).hexdigest() == doc["sha256"]
        finally:
            await fetcher.aclose()
    asyncio.run(run())


def test_lightweight_archive_keeps_scanning_after_a_wholly_closed_page():
    root = "https://buyer.example"
    start = root + "/notices"
    pages = {}
    states = ["open", "closed", "closed", "closed", "open"]
    for i, state in enumerate(states, 1):
        page_url = start if i == 1 else f"{start}?page={i}"
        following = f'<a rel="next" href="?page={i+1}">›</a>' if i < len(states) else ""
        pages[page_url] = (f'<main><article data-id="R-{i}"><a href="/n/{i}">Roof {i}</a>'
                           f'<span>{state}</span></article>{following}</main>')
        pages[f"{root}/n/{i}"] = (f'<main><h1 data-id="R-{i}">Roof {i}</h1><span>{state}</span>'
                                    f'<a download href="/blob/{i}">📎</a></main>')
    recipe = Recipe.model_validate({
        "list": {"url": start, "item": "//article", "next_page": "//a[@rel='next']/@href",
                 "fields": {"id": "./@data-id", "title": "./a", "status": "./span"}},
        "detail": {"link": "./a/@href"}, "fields": {"id": "//h1/@data-id", "title": "//h1", "status": "//span"},
        "documents": {"link": "//a[@download]/@href"},
    })
    staged = {}
    fetcher = FakeFetcher(pages)

    async def scan():
        try:
            for attempt in range(2):
                cache = RunPageCache(fetcher, "archive")
                cache.staged_notice = staged.get
                cache.stage_notice = staged.__setitem__
                fetcher.request_log.clear()
                rows = await run_recipe(recipe, cache)
                assert {r.fields["id"] for r in rows if r.document_links} == {"R-1", "R-5"}
                assert {r.fields["id"] for r in rows if r.archived_summary} == {"R-2", "R-3", "R-4"}
                requests = [u for _, _, u in fetcher.request_log]
                assert root + "/n/4" not in requests
                assert (root + "/n/5" in requests) == (attempt == 0)
                assert requests.count(start + "?page=5") == 1
                assert staged
        finally:
            await fetcher.aclose()
    asyncio.run(scan())


def test_widespread_css_buyer_fails_health():
    rows = [RawTender(url=f"https://buyer.example/{i}", fields={
        "id": str(i), "title": f"Roof {i}", "buyerName": ".header { color: black; }" if i < 2 else "City",
    }) for i in range(3)]
    invalid_fields = clean_short_fields(rows)
    result = check_health(None, ReadResult(tenders=[normalize(r, Locale()) for r in rows], invalid_fields=invalid_fields))
    assert invalid_fields == {"buyerName": 2}
    assert not result.ok and "buyerName" in " ".join(result.problems)


def test_split_zip_final_segment_stays_whole(tmp_path):
    import zipfile
    from app.documents import _finalize
    path = tmp_path / "download"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("last.pdf", b"%PDF-last")
    body = path.read_bytes()
    entries, skipped = _finalize(path, "spec.zip.003", len(body), hashlib.sha256(body).hexdigest(), tmp_path)
    assert skipped == 0 and len(entries) == 1
    assert entries[0].name == "spec.zip.003" and entries[0].path.read_bytes() == body


def test_rendered_file_button_is_observed_before_bytes_are_downloaded(monkeypatch):
    from app.fetcher import Fetcher
    from brain import js_discover
    from brain.run_recipe import rendered_file_source
    url = "https://buyer.example/notice/1"
    seen = []
    def handle(request):
        seen.append(str(request.url))
        assert str(request.url) == url
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=
                              """<main><button onclick="fetch('/d/1')">仕様.pdf</button></main>""")
    monkeypatch.setattr(js_discover, "_QUIET_SECONDS", 0.05)
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(handle), min_interval=0)
        try:
            _, files = await rendered_file_source(url, fetcher.for_portal("check"))
            assert files == {"https://buyer.example/d/1": "仕様.pdf"} and seen == [url]
        finally:
            await fetcher.aclose()
    asyncio.run(run())


def test_archive_filter_keeps_buyer_and_region_and_rejects_individual_phases():
    url = "https://buyer.example/notices?region=7&buyer=42"
    form = """<form action="/notices" method="get">
        <select name="buyer"><option selected value="42">Buyer A</option></select>
        <select name="state"><option selected value="all">全部</option><option value="active">公開中</option>
        <option value="finished">終了</option><option disabled>—</option>
        <option value="offers">応募</option><option value="qualify">資格</option></select></form>"""
    page = html.fromstring(form)
    from urllib.parse import parse_qsl, urlsplit
    assert dict(parse_qsl(urlsplit(archive_open_view(page, url)).query)) == {"buyer": "42", "region": "7", "state": "active"}
    assert archive_open_view(page, url.replace("42", "43")) is None
    assert archive_open_view(page, url + "&state=offers") is None
    assert archive_open_view(html.fromstring(form.replace('value="active"', 'value="phase1"')), url) is None


def test_only_the_primary_tab_can_supply_dossier_files():
    markup = """<main><a role="tab" href="#tab2">Pièces de marché</a><a role="tab" href="#tab4">Q&amp;A</a>
                <section id="tab4"><a download href="/answer.pdf">回答</a></section></main>"""
    page = html.fromstring(markup)
    with pytest.raises(RecipeReadError, match="primary dossier"):
        _document_panel(page, None)
    with pytest.raises(RecipeReadError, match="no public dossier panel"):
        _document_panel(page, "Pièces de marché")
    page.append(html.fromstring('<section id="tab2"><a download href="/dossier.pdf">Dossier</a></section>'))
    assert _document_panel(page, "Pièces de marché").xpath(".//a/@href") == ["/dossier.pdf"]


def test_browser_visits_localized_open_tab_and_refresh_replays_without_a_model(monkeypatch):
    from app.fetcher import Fetcher
    from brain import js_discover
    from brain.public_search import ObservedSearch

    monkeypatch.setattr(js_discover, "_JSON_WAIT_SECONDS", 0.15)
    monkeypatch.setattr(js_discover, "_QUIET_SECONDS", 0.1)
    monkeypatch.setattr(js_discover, "_EXTRA_CAPTURE_SECONDS", 0.01)
    root = "https://buyer.example"
    label = "Anuncis de licitació en termini"
    count = 37
    def handle(request):
        if request.method == "GET" and request.url.path == "/":
            return httpx.Response(200, text=f"""<meta charset="utf-8"><main><a href="">Comunicacions (3)</a>
              <a href="" onclick="event.preventDefault(); fetch('/api/search', {{method:'POST',
              headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{page:0,status:'open'}})}})">
              {label} ({count})</a></main>""", headers={"content-type": "text/html"})
        assert request.method == "POST" and request.url.path == "/api/search"
        return httpx.Response(200, json={"tenders": [{"id": "R-1", "title": "Roof", "buyer": "City"}], "total": 1})
    class Model:
        async def json(self, prompt, **kwargs):
            return {"index": json.loads(prompt)["public_listing_tabs"].index(label)}

    async def run():
        nonlocal count
        fetcher = Fetcher(transport=httpx.MockTransport(handle), min_interval=0)
        fetch = fetcher.for_portal("test")
        try:
            replies = await js_discover._capture_json(root + "/", fetch, llm=Model())
            search, payload = next((k, v) for k, v in replies if isinstance(k, ObservedSearch))
            assert search.spec.listing_tab == label
            count += 1
            again = await js_discover._capture_json(root + "/", fetch, target=search.spec)
            assert any(value == payload for _, value in again)
        finally:
            await fetcher.aclose()
    asyncio.run(run())
