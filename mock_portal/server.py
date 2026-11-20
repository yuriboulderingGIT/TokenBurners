"""A fake tender portal we can break on purpose. OWNER: A2.

Run:     uvicorn mock_portal.server:app --port 8090
Open:    http://localhost:8090/tenders

Switches (open in a browser or curl; they can be combined):
    /_switch?layout=v2        different HTML + different field labels -> recipe v1 breaks
    /_switch?layout=v1        back to normal
    /_switch?variant=captcha  every page shows a captcha -> portal must go to failed
    /_switch?variant=spa      JavaScript app: the list loads from the JSON API /api/tenders
    /_switch?variant=ssr      back to normal (server-rendered HTML)
    /_switch?docs=login       every document link leads to a login page -> failed, "login required"
    /_switch?docs=big         MOCK-005 also links a 300 MB ZIP -> must be skipped cleanly
    /_switch?docs=normal      back to normal
    /_switch?dead=true       MOCK-005 stays listed but its tender page returns 404
    /_switch?moved=true      list moves to /notices, old /tenders returns 404; / links to it
    /_switch?https=true      HTTP list requests redirect to HTTPS (serve TLS for a live test)
    /_switch?reset=1          everything back to normal
    /_state                   show current settings

Documents: every tender has a PDF. MOCK-003 also has a ZIP package that contains a PDF,
an Excel file (must NOT be unpacked) and a nested ZIP with one more PDF.
"""

import io
import zipfile
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

app = FastAPI(title="Mock tender portal")

DEFAULT_STATE = {"layout": "v1", "variant": "ssr", "docs": "normal", "dead": False, "moved": False, "https": False}
STATE = dict(DEFAULT_STATE)
PAGE_SIZE = 3
BIG_FILE_BYTES = 300 * 1024 * 1024

TENDERS = [
    {"id": "MOCK-001", "ref": "PR-2026-001", "title": "Road resurfacing, Main Street",
     "buyer": "City of Sampletown", "published": "20 Nov 2026", "deadline": "09 Dec 2026, 10:00",
     "cpv": "45233140-2"},
    {"id": "MOCK-002", "ref": "PR-2026-002", "title": "Cleaning services for North Primary School",
     "buyer": "City of Sampletown", "published": "18 Nov 2026", "deadline": "15 Dec 2026, 12:00",
     "cpv": "90919200-4"},
    {"id": "MOCK-003", "ref": "PR-2026-003", "title": "Supply of office furniture",
     "buyer": "Example County Council", "published": "17 Nov 2026", "deadline": "05 Jan 2027, 09:30",
     "cpv": "39130000-2"},
    {"id": "MOCK-004", "ref": "PR-2026-004", "title": "IT maintenance for the town hall",
     "buyer": "Testville Municipality", "published": "15 Nov 2026", "deadline": "11 Dec 2026, 14:00",
     "cpv": "72250000-2"},
    {"id": "MOCK-005", "ref": "PR-2026-005", "title": "Construction of a new fire station",
     "buyer": "Testville Municipality", "published": "12 Nov 2026", "deadline": "20 Jan 2027, 11:00",
     "cpv": "45216121-8"},
]

PACKAGE_TENDER = "MOCK-003"
BIG_TENDER = "MOCK-005"

CAPTCHA_PAGE = """<html><body><h1>Please confirm you are not a robot</h1>
<form><div class="g-recaptcha" data-sitekey="mock"></div><button>Continue</button></form></body></html>"""

LOGIN_PAGE = """<!doctype html><html><head><title>Sign in</title></head><body><h1>Sign in</h1>
<p>Please sign in to download the tender documents.</p>
<form method="post"><input name="user"><input type="password" name="pass"><button>Sign in</button></form>
</body></html>"""

SPA_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Tenders</title></head><body>
<h1>Current tenders</h1><div id="app">Loading...</div>
<script>
async function load(url) {
  const data = await (await fetch(url)).json();
  const app = document.getElementById("app");
  app.innerHTML = data.items.map(t =>
    `<div class="tender"><a href="${t.url}">${t.title}</a> <span>${t.deadline}</span></div>`).join("");
  if (data.next) { app.innerHTML += `<button onclick="load('${data.next}')">Next</button>`; }
}
load("/api/tenders?page=1");
</script></body></html>"""


def _state(key: str) -> str | bool:
    """Tests may replace STATE with a dict that lacks newer keys."""
    return STATE.get(key, DEFAULT_STATE[key])


def _page(title: str, body: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>'


def _chunk(page: int) -> tuple[list[dict], bool]:
    start = (page - 1) * PAGE_SIZE
    return TENDERS[start:start + PAGE_SIZE], start + PAGE_SIZE < len(TENDERS)


def render_list(page: int = 1, layout: str = "v1") -> str:
    path = _list_path()
    chunk, has_next = _chunk(page)
    if layout == "v1":
        rows = "".join(
            f'<tr><td><a href="/tender/{t["id"]}">{t["title"]}</a></td><td>{t["deadline"]}</td></tr>'
            for t in chunk
        )
        pager = f'<a rel="next" href="{path}?page={page + 1}">Next</a>' if has_next else ""
        body = (f"<h1>Current tenders</h1><table id=\"results\">"
                f"<tr><th>Title</th><th>Deadline</th></tr>{rows}</table>{pager}")
    else:
        cards = "".join(
            f'<div class="card"><h3><a href="/t/{t["id"]}">{t["title"]}</a></h3>'
            f'<span class="due">{t["deadline"]}</span></div>'
            for t in chunk
        )
        pager = f'<a class="pager-next" href="{path}?page={page + 1}">Next page</a>' if has_next else ""
        body = f'<h2>Tenders</h2><div class="results">{cards}</div>{pager}'
    return _page("Tenders", body)


def _document_links(t: dict, layout: str) -> list[tuple[str, str]]:
    """(href, label) for one tender's documents in the given layout."""
    files = ["tender-documents.pdf"]
    if t["id"] == PACKAGE_TENDER:
        files.append("tender-package.zip")
    if t["id"] == BIG_TENDER and _state("docs") == "big":
        files.append("site-survey.zip")
    if layout == "v1":
        return [(f"/download/{t['id']}/{name}", f"Download {name}") for name in files]
    return [(f"/dl?tender={t['id']}&amp;file={name}", name) for name in files]


def render_detail(tender_id: str, layout: str = "v1") -> str | None:
    t = next((t for t in TENDERS if t["id"] == tender_id), None)
    if t is None:
        return None
    if layout == "v1":
        links = "".join(f'<a href="{href}">{label}</a> ' for href, label in _document_links(t, layout))
        body = (
            f"<h1>{t['title']}</h1><dl>"
            f"<dt>Tender ID</dt><dd>{t['id']}</dd>"
            f"<dt>Reference number</dt><dd>{t['ref']}</dd>"
            f"<dt>Contracting authority</dt><dd>{t['buyer']}</dd>"
            f"<dt>Published on</dt><dd>{t['published']}</dd>"
            f"<dt>Submission deadline</dt><dd>{t['deadline']}</dd>"
            f"<dt>CPV code</dt><dd>{t['cpv']}</dd></dl>{links}"
        )
    else:
        links = "".join(f'<a class="doc" href="{href}">{label}</a> ' for href, label in _document_links(t, layout))
        body = (
            f'<h2 class="title">{t["title"]}</h2><table class="facts">'
            f"<tr><th>Identifier</th><td>{t['id']}</td></tr>"
            f"<tr><th>Ref.</th><td>{t['ref']}</td></tr>"
            f"<tr><th>Buyer</th><td>{t['buyer']}</td></tr>"
            f"<tr><th>Publication</th><td>{t['published']}</td></tr>"
            f"<tr><th>Closing date</th><td>{t['deadline']}</td></tr>"
            f"<tr><th>CPV</th><td>{t['cpv']}</td></tr></table>{links}"
        )
    return _page(t["title"], body)


# ---------- document files ----------


def _pdf(text: str) -> bytes:
    return f"%PDF-1.4\n% {text}\n%%EOF\n".encode()


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _xlsx_like() -> bytes:
    """A minimal ZIP container shaped like an Excel file: it must stay one document."""
    return _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})


PACKAGE_ZIP = _zip({
    "specification.pdf": _pdf("technical specification"),
    "forms/price-schedule.xlsx": _xlsx_like(),
    "annexes.zip": _zip({"annex-1-site-plan.pdf": _pdf("annex 1")}),
    "__MACOSX/._specification.pdf": b"junk",
})
# What a correct pipeline delivers for the package (archives unpacked, junk dropped).
PACKAGE_FILES = {"specification.pdf", "price-schedule.xlsx", "annex-1-site-plan.pdf"}


def _zeros(total: int) -> Iterator[bytes]:
    block = b"\0" * (1024 * 1024)
    sent = 0
    while sent < total:
        part = block[: min(len(block), total - sent)]
        sent += len(part)
        yield part


def _file_response(tender_id: str, file_name: str) -> Response:
    if not any(t["id"] == tender_id for t in TENDERS):
        raise HTTPException(404)
    if _state("docs") == "login":
        return HTMLResponse(LOGIN_PAGE)
    disposition = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    if file_name == "tender-documents.pdf":
        return Response(_pdf(f"mock tender documents for {tender_id}"), media_type="application/pdf",
                        headers=disposition)
    if file_name == "tender-package.zip" and tender_id == PACKAGE_TENDER:
        return Response(PACKAGE_ZIP, media_type="application/zip", headers=disposition)
    if file_name == "site-survey.zip" and tender_id == BIG_TENDER:
        return StreamingResponse(_zeros(BIG_FILE_BYTES), media_type="application/zip",
                                 headers={**disposition, "Content-Length": str(BIG_FILE_BYTES)})
    raise HTTPException(404)


# ---------- routes ----------


@app.get("/_switch")
def switch(layout: str | None = None, variant: str | None = None, docs: str | None = None,
           dead: bool | None = None, moved: bool | None = None, https: bool | None = None,
           reset: bool = False) -> dict:
    if reset:
        STATE.clear()
        STATE.update(DEFAULT_STATE)
    for key, value in (("dead", dead), ("moved", moved), ("https", https)):
        if value is not None:
            STATE[key] = value
    if layout in ("v1", "v2"):
        STATE["layout"] = layout
    if variant in ("ssr", "captcha", "spa"):
        STATE["variant"] = variant
    if docs in ("normal", "login", "big"):
        STATE["docs"] = docs
    return {key: _state(key) for key in DEFAULT_STATE}


@app.get("/_state")
def state() -> dict:
    return {key: _state(key) for key in DEFAULT_STATE}


def _list_path() -> str:
    return "/notices" if _state("moved") else "/tenders"


@app.get("/", response_class=HTMLResponse)
def home():
    return _page("Procurement", f'<a href="{_list_path()}">Current tenders</a>')


@app.get("/notices", response_class=HTMLResponse)
@app.get("/tenders", response_class=HTMLResponse)
def tenders(request: Request, page: int = 1):
    if _state("https") and request.url.scheme == "http":
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
    if request.url.path != _list_path():
        raise HTTPException(404)
    if _state("variant") == "captcha":
        return HTMLResponse(CAPTCHA_PAGE)
    if _state("variant") == "spa":
        return HTMLResponse(SPA_PAGE)
    return HTMLResponse(render_list(page, _state("layout")))


@app.get("/api/tenders")
def api_tenders(page: int = 1):
    """The JSON feed behind the JavaScript variant."""
    if _state("variant") != "spa":
        raise HTTPException(404)
    chunk, has_next = _chunk(page)
    return JSONResponse({
        "items": [
            {
                "id": t["id"], "reference": t["ref"], "title": t["title"], "buyer": t["buyer"],
                "published": t["published"], "deadline": t["deadline"], "cpv": t["cpv"],
                "url": f"/tender/{t['id']}",
                "documents": [{"href": href} for href, _ in _document_links(t, "v1")],
            }
            for t in chunk
        ],
        "next": f"/api/tenders?page={page + 1}" if has_next else None,
    })


@app.get("/tender/{tender_id}", response_class=HTMLResponse)
@app.get("/t/{tender_id}", response_class=HTMLResponse)
def detail(tender_id: str):
    if _state("variant") == "captcha":
        return HTMLResponse(CAPTCHA_PAGE)
    if _state("dead") and tender_id == "MOCK-005":
        raise HTTPException(404)
    html = render_detail(tender_id, _state("layout"))
    if html is None:
        raise HTTPException(404)
    return HTMLResponse(html)


@app.get("/download/{tender_id}/{file_name}")
def download(tender_id: str, file_name: str):
    return _file_response(tender_id, file_name)


@app.get("/dl")
def download_v2(tender: str, file: str):
    return _file_response(tender, file)
