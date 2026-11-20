"""Document pipeline (P7). OWNER: A2."""

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from app import documents
from app.db import DB
from app.fetcher import Fetcher, PortalCancelled
from app.models import Tender
from mock_portal import server

BASE = "http://portal.example"


def _tender(tender_id: str = "T-1") -> Tender:
    return Tender(id=tender_id, url=f"{BASE}/t/{tender_id}", title="x", buyerName=None,
                  publicationDate=None, submissionDeadline=None, status="open")


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _store(db: DB, routes: dict, links: list[str], tender: Tender | None = None, requests: list | None = None):
    """routes: path -> httpx.Response (or a callable returning one)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(str(request.url))
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        return route() if callable(route) else route

    async def run():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(handler))
        try:
            return await documents.store_documents(db, "p1", tender or _tender(), links, fetcher.for_portal("p1"))
        finally:
            await fetcher.aclose()

    return asyncio.run(run())


@pytest.fixture
def db(tmp_path, monkeypatch) -> DB:
    monkeypatch.setattr(documents, "FILES_DIR", tmp_path / "files")
    return DB(tmp_path / "docs.sqlite")


def _served_bytes(db: DB, path: str) -> bytes:
    row = db.document_get(path.removeprefix("/files/"))
    return Path(row["path_on_disk"]).read_bytes()


def test_plain_file_is_stored_with_name_size_and_hash(db) -> None:
    pdf = b"%PDF-1.4 hello"
    routes = {"/files/spec.pdf": httpx.Response(200, content=pdf)}
    result = _store(db, routes, [f"{BASE}/files/spec.pdf"])
    assert result.failed == result.blocked == result.too_large == 0
    [doc] = result.documents
    assert doc.fileName == "spec.pdf"
    assert doc.sizeBytes == len(pdf)
    assert doc.sha256 == hashlib.sha256(pdf).hexdigest()
    assert doc.path.startswith("/files/")
    assert _served_bytes(db, doc.path) == pdf


def test_name_comes_from_content_disposition_then_query(db) -> None:
    routes = {
        "/download": httpx.Response(200, content=b"%PDF a", headers={"content-disposition": 'attachment; filename="Lot 1.pdf"'}),
        "/dl": httpx.Response(200, content=b"%PDF b"),
    }
    result = _store(db, routes, [f"{BASE}/download?id=7", f"{BASE}/dl?tender=X&file=plans.pdf"])
    assert [d.fileName for d in result.documents] == ["Lot 1.pdf", "plans.pdf"]


def test_zip_is_unpacked_including_nested_but_office_files_stay_whole(db) -> None:
    result = _store(db, {"/p.zip": httpx.Response(200, content=server.PACKAGE_ZIP)}, [f"{BASE}/p.zip"])
    assert {d.fileName for d in result.documents} == server.PACKAGE_FILES
    xlsx = next(d for d in result.documents if d.fileName.endswith(".xlsx"))
    assert zipfile.is_zipfile(io.BytesIO(_served_bytes(db, xlsx.path)))  # delivered as the Excel file itself
    for doc in result.documents:
        assert hashlib.sha256(_served_bytes(db, doc.path)).hexdigest() == doc.sha256


def test_zip_without_zip_extension_is_detected_by_content(db) -> None:
    archive = _zip({"a.pdf": b"%PDF a", "b.pdf": b"%PDF b"})
    result = _store(db, {"/getfile": httpx.Response(200, content=archive)}, [f"{BASE}/getfile?id=1"])
    assert {d.fileName for d in result.documents} == {"a.pdf", "b.pdf"}


def test_docx_named_file_is_never_unpacked(db) -> None:
    docx = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
    result = _store(db, {"/letter.docx": httpx.Response(200, content=docx)}, [f"{BASE}/letter.docx"])
    assert [d.fileName for d in result.documents] == ["letter.docx"]


def test_broken_zip_is_delivered_as_is(db) -> None:
    broken = _zip({"a.pdf": b"%PDF a"})[:-30]
    result = _store(db, {"/bad.zip": httpx.Response(200, content=broken)}, [f"{BASE}/bad.zip"])
    assert [d.fileName for d in result.documents] == ["bad.zip"]


def test_file_over_the_cap_is_skipped_by_header_and_while_streaming(db, monkeypatch) -> None:
    monkeypatch.setattr(documents, "MAX_DOC_BYTES", 1000)

    async def chunks():  # no Content-Length: the cap must be enforced while streaming
        for _ in range(5):
            yield b"x" * 1000

    routes = {
        "/announced.zip": httpx.Response(200, headers={"content-length": str(300 * 1024 * 1024)},
                                         stream=httpx.ByteStream(b"")),
        "/unannounced.bin": lambda: httpx.Response(200, content=chunks()),
        "/small.pdf": httpx.Response(200, content=b"%PDF ok"),
    }
    links = [f"{BASE}/announced.zip", f"{BASE}/unannounced.bin", f"{BASE}/small.pdf"]
    result = _store(db, routes, links)
    assert result.too_large == 2
    assert result.failed == 0
    assert [d.fileName for d in result.documents] == ["small.pdf"]


def test_zip_member_over_the_cap_is_skipped(db, monkeypatch) -> None:
    monkeypatch.setattr(documents, "MAX_DOC_BYTES", 1000)
    archive = _zip({"huge.pdf": b"x" * 5000, "ok.pdf": b"%PDF ok"})
    result = _store(db, {"/p.zip": httpx.Response(200, content=archive)}, [f"{BASE}/p.zip"])
    assert [d.fileName for d in result.documents] == ["ok.pdf"]
    assert result.too_large == 1


def test_zip_bomb_budget_stops_unpacking(db, monkeypatch) -> None:
    monkeypatch.setattr(documents, "MAX_UNPACKED_BYTES", 6000)
    archive = _zip({f"part{i}.pdf": b"x" * 2500 for i in range(5)})
    result = _store(db, {"/p.zip": httpx.Response(200, content=archive)}, [f"{BASE}/p.zip"])
    assert len(result.documents) == 2
    assert result.too_large == 3


def test_http_errors_count_as_failed_and_401_as_login(db) -> None:
    routes = {"/gone.pdf": httpx.Response(500), "/private.pdf": httpx.Response(401)}
    result = _store(db, routes, [f"{BASE}/gone.pdf", f"{BASE}/private.pdf", f"{BASE}/missing.pdf"])
    assert result.documents == []
    assert result.failed == 2
    assert result.blocked == 1 and result.block_kind == "login"


def test_login_page_instead_of_file_is_blocked_and_ordinary_page_is_failed(db) -> None:
    routes = {
        "/doc.pdf": httpx.Response(200, text=server.LOGIN_PAGE),
        "/notice": httpx.Response(200, text="<!doctype html><html><body><h1>Notice</h1>" + "<a href='/x'>x</a>" * 5 + "</body></html>"),
    }
    result = _store(db, routes, [f"{BASE}/doc.pdf", f"{BASE}/notice"])
    assert result.documents == []
    assert result.blocked == 1 and result.block_kind == "login"
    assert result.failed == 1


def test_second_read_reuses_files_without_requests(db) -> None:
    routes = {"/a.pdf": httpx.Response(200, content=b"%PDF a"), "/p.zip": httpx.Response(200, content=server.PACKAGE_ZIP)}
    links = [f"{BASE}/a.pdf", f"{BASE}/p.zip"]
    first = _store(db, routes, links)
    requests: list[str] = []
    second = _store(db, routes, links, requests=requests)
    assert requests == []
    assert second.documents == first.documents


def test_missing_file_on_disk_is_downloaded_again(db) -> None:
    routes = {"/a.pdf": httpx.Response(200, content=b"%PDF a")}
    first = _store(db, routes, [f"{BASE}/a.pdf"])
    Path(db.document_get(first.documents[0].path.removeprefix("/files/"))["path_on_disk"]).unlink()
    requests: list[str] = []
    second = _store(db, routes, [f"{BASE}/a.pdf"], requests=requests)
    assert requests == [f"{BASE}/a.pdf"]
    assert _served_bytes(db, second.documents[0].path) == b"%PDF a"
    assert db.document_get(first.documents[0].path.removeprefix("/files/")) is None


def test_same_link_for_two_tenders_is_stored_per_tender(db) -> None:
    routes = {"/shared.pdf": httpx.Response(200, content=b"%PDF s")}
    a = _store(db, routes, [f"{BASE}/shared.pdf"], tender=_tender("A"))
    b = _store(db, routes, [f"{BASE}/shared.pdf"], tender=_tender("B"))
    assert a.documents[0].path != b.documents[0].path


def test_deleted_portal_stops_the_download(db) -> None:
    async def run():
        fetcher = Fetcher(min_interval=0, retries=0, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        fetcher.cancel("p1")
        try:
            await documents.store_documents(db, "p1", _tender(), [f"{BASE}/a.pdf"], fetcher.for_portal("p1"))
        finally:
            await fetcher.aclose()

    with pytest.raises(PortalCancelled):
        asyncio.run(run())
