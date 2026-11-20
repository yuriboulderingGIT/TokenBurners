"""Exercise document recovery through an ordinary worker read."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.db import DB
from app.fetcher import FakeFetcher
from app.models import Recipe
from app.worker import Worker
from brain.learn import learn_recipe


_LIST = "https://procurement.example/notices"
_PDF = b"%PDF-1.7\nfixture"


async def _read(db, pages, recipe):
    portal_id = db.portal_create(_LIST)
    db.recipe_save(portal_id, recipe, "attach", 0)
    fetcher = FakeFetcher(pages)
    worker = Worker(db, fetcher, llm=None)
    try:
        await worker._read(portal_id, db.run_create(portal_id, "read"), allow_repair=False)
        return portal_id, [url for _, _, url in fetcher.request_log]
    finally:
        await fetcher.aclose()


def test_attach_samples_the_notice_when_the_row_also_links_to_a_pdf():
    notice = "https://procurement.example/notice/1"

    class Model:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                return {"list_url": _LIST}
            assert evidence["detail_page"]["url"] == notice
            return {"list": {"url": _LIST, "item": "//tr[td]"},
                    "detail": {"link": "./td[1]/a/@href"},
                    "fields": {"id": "//h1/@data-id", "title": "//h1"}}

    async def attach():
        fetcher = FakeFetcher({
            _LIST: """<table><tr><td><a href="/notice/1">Road works</a></td><td>T1</td>
                  <td><a href="/file?id=1"><img alt="Contract Notice PDF"></a></td></tr></table>""",
            notice: '<h1 data-id="T1">Road works</h1>',
        })
        try:
            assert (await learn_recipe(_LIST, fetcher.for_portal("fixture"), Model())).detail.link
        finally:
            await fetcher.aclose()

    asyncio.run(attach())


@pytest.mark.parametrize("use_detail_selector", [False, True])
def test_delivers_documents_from_an_observed_ezak_notice_and_its_listing(tmp_path, monkeypatch, use_detail_selector):
    monkeypatch.setattr("app.documents.FILES_DIR", tmp_path / "files")
    db = DB(tmp_path / "watchtower.db")
    host = "https://procurement.example"
    detail = host + "/contract_display_8518.html"
    notice_pdf = host + "/published/notice"
    attachment = host + "/document_156896/aa-specs-pdf"
    pages = {
        _LIST: '''<article data-id="8518"><h2><a href="/contract_display_8518.html">Supply of lab equipment</a></h2>
          <a href="/published/notice"><img alt="Contract Notice PDF"></a></article>''',
        detail: '''<h1 data-id="8518">Supply of lab equipment</h1><a href="document_download_156896.html">Document details</a>
          <a href="document_156896/aa-specs-pdf">Technical specifications.pdf</a>
          <a href="https://unrelated.example/report.pdf">Report.pdf</a>
          <footer><a href="/privacy.pdf">Privacy.pdf</a></footer>''',
        notice_pdf: _PDF, attachment: _PDF,
    }
    recipe = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//article"},
        "detail": {"link": "./h2/a/@href" if use_detail_selector else None},
        "documents": {"link": "//a[contains(@href, 'document_download_')]/@href"} if use_detail_selector else {},
        "fields": {"id": "//h1/@data-id", "title": "//h1"} if use_detail_selector else {
            "id": "@data-id", "title": "./h2",
        },
    })
    portal, urls = asyncio.run(_read(db, pages, recipe))
    row = db.portal_get(portal)
    assert row["state"] == "serving", row["reason"]
    [tender] = db.tenders_for(portal)
    assert tender["id"] == "8518" and tender["url"] == detail
    assert set(urls) == {_LIST, detail, notice_pdf, attachment}
    assert len(tender["documents"]) == 2
    for doc in tender["documents"]:
        assert doc["sha256"] == hashlib.sha256(_PDF).hexdigest()
        assert Path(db.document_get(doc["path"].split("/")[-1])["path_on_disk"]).read_bytes() == _PDF


@pytest.mark.parametrize("has_link", [True, False])
def test_reports_no_files_when_download_is_broken_or_listing_has_no_links(tmp_path, monkeypatch, has_link):
    monkeypatch.setattr("app.documents.FILES_DIR", tmp_path / "files")
    db = DB(tmp_path / "watchtower.db")
    link = '<a download href="/gone.pdf">Specs.pdf</a>' if has_link else ""
    pages = {_LIST: f'<article data-id="T1"><h2>Library roof</h2>{link}</article>'}
    recipe = Recipe.model_validate({
        "list": {"url": _LIST, "item": "//article"}, "fields": {"id": "@data-id", "title": "./h2"},
    })
    portal, _ = asyncio.run(_read(db, pages, recipe))
    row = db.portal_get(portal)
    if has_link:
        assert row["state"] == "failed"
        assert "1 of 1 document downloads failed" in row["reason"]
        assert db.tenders_for(portal) == []
    else:
        assert row["state"] == "serving"
        assert "No document links found" in row["reason"]
