"""A fake tender portal we can break on purpose. OWNER: A2.

Run:     uvicorn mock_portal.server:app --port 8090
Open:    http://localhost:8090/tenders

Switches (open in a browser or curl):
    /_switch?layout=v2        different HTML + different field labels -> recipe v1 breaks
    /_switch?layout=v1        back to normal
    /_switch?variant=captcha  every page shows a captcha -> portal must go to failed
    /_switch?variant=ssr      back to normal
    /_state                   show current settings

TODO(A2, P8): spa_json variant (JS app that loads tenders from /api/tenders),
              a ZIP document, a 300 MB document to test the size cap,
              later maybe a non-English variant (real portals are multilingual).
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="Mock tender portal")

STATE = {"layout": "v1", "variant": "ssr"}
PAGE_SIZE = 3

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

CAPTCHA_PAGE = """<html><body><h1>Please confirm you are not a robot</h1>
<form><div class="g-recaptcha" data-sitekey="mock"></div><button>Continue</button></form></body></html>"""


def _page(title: str, body: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>'


def render_list(page: int = 1, layout: str = "v1") -> str:
    start = (page - 1) * PAGE_SIZE
    chunk = TENDERS[start:start + PAGE_SIZE]
    has_next = start + PAGE_SIZE < len(TENDERS)
    if layout == "v1":
        rows = "".join(
            f'<tr><td><a href="/tender/{t["id"]}">{t["title"]}</a></td><td>{t["deadline"]}</td></tr>'
            for t in chunk
        )
        pager = f'<a rel="next" href="/tenders?page={page + 1}">Next</a>' if has_next else ""
        body = (f"<h1>Current tenders</h1><table id=\"results\">"
                f"<tr><th>Title</th><th>Deadline</th></tr>{rows}</table>{pager}")
    else:
        cards = "".join(
            f'<div class="card"><h3><a href="/t/{t["id"]}">{t["title"]}</a></h3>'
            f'<span class="due">{t["deadline"]}</span></div>'
            for t in chunk
        )
        pager = f'<a class="pager-next" href="/tenders?page={page + 1}">Next page</a>' if has_next else ""
        body = f'<h2>Tenders</h2><div class="results">{cards}</div>{pager}'
    return _page("Tenders", body)


def render_detail(tender_id: str, layout: str = "v1") -> str | None:
    t = next((t for t in TENDERS if t["id"] == tender_id), None)
    if t is None:
        return None
    if layout == "v1":
        body = (
            f"<h1>{t['title']}</h1><dl>"
            f"<dt>Tender ID</dt><dd>{t['id']}</dd>"
            f"<dt>Reference number</dt><dd>{t['ref']}</dd>"
            f"<dt>Contracting authority</dt><dd>{t['buyer']}</dd>"
            f"<dt>Published on</dt><dd>{t['published']}</dd>"
            f"<dt>Submission deadline</dt><dd>{t['deadline']}</dd>"
            f"<dt>CPV code</dt><dd>{t['cpv']}</dd></dl>"
            f'<a href="/download/{t["id"]}/tender-documents.pdf">Download tender documents</a>'
        )
    else:
        body = (
            f'<h2 class="title">{t["title"]}</h2><table class="facts">'
            f"<tr><th>Identifier</th><td>{t['id']}</td></tr>"
            f"<tr><th>Ref.</th><td>{t['ref']}</td></tr>"
            f"<tr><th>Buyer</th><td>{t['buyer']}</td></tr>"
            f"<tr><th>Publication</th><td>{t['published']}</td></tr>"
            f"<tr><th>Closing date</th><td>{t['deadline']}</td></tr>"
            f"<tr><th>CPV</th><td>{t['cpv']}</td></tr></table>"
            f'<a class="doc" href="/dl?tender={t["id"]}&amp;file=documents.pdf">Documents</a>'
        )
    return _page(t["title"], body)


def _doc_bytes(tender_id: str) -> bytes:
    return f"%PDF-1.4\n% mock tender documents for {tender_id}\n%%EOF\n".encode()


@app.get("/_switch")
def switch(layout: str | None = None, variant: str | None = None) -> dict:
    if layout in ("v1", "v2"):
        STATE["layout"] = layout
    if variant in ("ssr", "captcha"):
        STATE["variant"] = variant
    return STATE


@app.get("/_state")
def state() -> dict:
    return STATE


@app.get("/tenders", response_class=HTMLResponse)
def tenders(page: int = 1):
    if STATE["variant"] == "captcha":
        return HTMLResponse(CAPTCHA_PAGE)
    return HTMLResponse(render_list(page, STATE["layout"]))


@app.get("/tender/{tender_id}", response_class=HTMLResponse)
@app.get("/t/{tender_id}", response_class=HTMLResponse)
def detail(tender_id: str):
    if STATE["variant"] == "captcha":
        return HTMLResponse(CAPTCHA_PAGE)
    html = render_detail(tender_id, STATE["layout"])
    if html is None:
        raise HTTPException(404)
    return HTMLResponse(html)


@app.get("/download/{tender_id}/{file_name}")
def download(tender_id: str, file_name: str):
    return Response(_doc_bytes(tender_id), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


@app.get("/dl")
def download_v2(tender: str, file: str):
    return download(tender, file)
