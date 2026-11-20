import asyncio
import json
from pathlib import Path

import httpx

from app.db import DB
from app.fetcher import Fetcher
from app.models import Recipe
from app.worker import Worker
from lxml import html
from brain.charite_documents import answers_url


def test_berlin_delegated_documents_are_downloaded_for_the_matching_notice(tmp_path, monkeypatch):
    listing = "https://www.berlin.de/vergabeplattform/veroeffentlichungen/bekanntmachungen/"
    bridge = "https://meinauftrag.rib.de/public/DetailsByPlatformIdAndTenderId/platformId/2/tenderId/"
    detail = "https://meinauftrag.rib.de/public/publications/615000"
    document = "https://my.vergabeplattform.berlin.de/remote/download.php?k=" + "a" * 32
    pdf = b"%PDF-1.7\nfixture"
    links = f'<a href="{document}">Specifications</a><a href="https://unrelated.example/report.pdf">Other</a>'
    grid = [{"data": [{"value": "Attachments"}], "rows": [{"data": [{"value": links}]}]}]
    routes = {
        listing: httpx.Response(200, text=f"""
            <article data-id="12"><h3>Bridge inspection</h3><button data-href="{bridge}12"></button></article>
            <article data-id="13"><h3>Library repairs</h3><button data-href="{bridge}99"></button></article>
        """),
        bridge + "12": httpx.Response(302, headers={"location": detail}),
        detail: httpx.Response(200, text=f"<script>\nvar documentsAttachments = {json.dumps(grid)};\n</script>"),
        document: httpx.Response(200, content=pdf, headers={
            "content-type": "application/pdf", "content-disposition": 'attachment; filename="specs.pdf"',
        }),
    }
    recipe = Recipe.model_validate({"list": {"url": listing, "item": "//article"},
                                    "fields": {"id": "@data-id", "title": "./h3"}})
    db = DB(tmp_path / "berlin.sqlite")
    portal = db.portal_create(listing)
    db.recipe_save(portal, recipe, "attach", 0)
    monkeypatch.setattr("app.documents.FILES_DIR", tmp_path / "files")

    async def exercise():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(
            lambda request: routes[str(request.url)],
        ))
        worker = Worker(db, fetcher, llm=None)
        try:
            worker.request_read(portal)
            await worker._handle(*worker.queue.get_nowait())
            assert db.portal_get(portal)["state"] == "serving"
            notices = {row["id"]: row for row in db.tenders_for(portal)}
            assert notices["12"]["url"] == bridge + "12"
            assert notices["13"]["documents"] == []
            [file] = notices["12"]["documents"]
            assert file["fileName"] == "specs.pdf"
            assert Path(db.document_get(file["path"].removeprefix("/files/"))["path_on_disk"]).read_bytes() == pdf
            assert [url for _, _, url in fetcher.request_log] == [listing, bridge + "12", detail, document]
        finally:
            await fetcher.aclose()

    asyncio.run(exercise())


def test_charite_answers_link_uses_the_matching_public_record():
    url = "https://vergabeplattform.charite.de/index.php"
    record = html.fromstring('''<tr data-tendersid="10003030"><td>RF 12/26</td>
      <td><div onclick="cep_downloadTenderDocuments('form')"></div>
      <div onclick="cep_downloadAnswers(10003030);"></div></td></tr>''')
    file_url = "https://vergabeplattform.charite.de/download/cep_downloadAnswers.php?sid=10003030"
    assert answers_url(url, record, "RF 12/26") == file_url
    assert answers_url(url, record, "10003030") == file_url
    assert answers_url(url, record, "incorrect") is None
    assert answers_url("https://unrelated.example/index.php", record, "RF 12/26") is None
    record.xpath(".//div")[1].set("onclick", "cep_downloadAnswers(999999);")
    assert answers_url(url, record, "RF 12/26") is None
