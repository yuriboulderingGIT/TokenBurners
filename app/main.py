"""API routes, exactly per plan section 3. OWNER: A1.

Run locally:  uvicorn app.main:app --port 8080 --reload
"""

import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.db import close_if_past_deadline, db
from app.fetcher import Fetcher
from app.models import CreatePortal, PortalOut, RunOut
from app.worker import Worker
from brain.llm import LLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _ago(value: str | None) -> str:
    """'2026-11-20T09:00:00+00:00' -> '4 min ago' for the dashboard."""
    if not value:
        return "never"
    seconds = int((datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _when(value: str | None) -> str:
    """'2026-12-09T10:00:00+01:00' -> '09 Dec 2026, 10:00 +01:00' (the portal's own local time)."""
    if not value:
        return "not shown"
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%d %b %Y, %H:%M ") + parsed.strftime("%z")[:3] + ":" + parsed.strftime("%z")[3:]


def _day(value: str | None) -> str:
    return datetime.fromisoformat(value).strftime("%d %b %Y") if value else "–"


def _size(n: int | None) -> str:
    if n is None:
        return ""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


templates.env.filters.update(ago=_ago, when=_when, day=_day, size=_size)


@asynccontextmanager
async def lifespan(app: FastAPI):
    fetcher = Fetcher()
    worker = Worker(db, fetcher, LLM(db))
    app.state.worker = worker
    await worker.start()
    yield
    await worker.stop()
    await fetcher.aclose()


app = FastAPI(title="Watchtower", lifespan=lifespan)


def _worker(request: Request) -> Worker:
    return request.app.state.worker


def _portal_or_404(portal_id: str):
    portal = db.portal_get(portal_id)
    if portal is None:
        raise HTTPException(status_code=404, detail="portal not found")
    return portal


def _portal_out(row) -> dict:
    return PortalOut(
        id=row["id"],
        url=row["url"],
        state=row["state"],
        reason=row["reason"],
        tenderCount=row["tender_count"],
        lastSuccessAt=row["last_success_at"],
    ).model_dump()


@app.post("/portals", status_code=201)
async def create_portal(body: CreatePortal, request: Request) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    worker = _worker(request)
    # ".../tenders" and ".../tenders/" are one portal; keep whichever form was posted first
    twin = url[:-1] if url.endswith("/") else url + "/"
    existing = db.portal_by_url(url) or db.portal_by_url(twin)
    if existing is not None and existing["deleted_at"] is None:
        return {"id": existing["id"]}  # same address -> same id
    if existing is not None:
        portal_id = existing["id"]
        db.portal_revive(portal_id)
        worker.revive(portal_id)
    else:
        try:
            portal_id = db.portal_create(url)
        except sqlite3.IntegrityError:  # same URL posted twice at the same moment
            return {"id": db.portal_by_url(url)["id"]}
    worker.request_read(portal_id)
    return {"id": portal_id}


@app.get("/portals")
async def list_portals() -> list[dict]:
    return [_portal_out(row) for row in db.portal_list()]


@app.get("/portals/{portal_id}")
async def get_portal(portal_id: str) -> dict:
    return _portal_out(_portal_or_404(portal_id))


@app.delete("/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: str, request: Request) -> Response:
    _portal_or_404(portal_id)
    db.portal_delete(portal_id)
    _worker(request).cancel(portal_id)
    return Response(status_code=204)


@app.post("/portals/{portal_id}/refresh", status_code=202)
async def refresh_portal(portal_id: str, request: Request) -> dict:
    _portal_or_404(portal_id)
    return {"runId": _worker(request).request_read(portal_id)}


@app.get("/portals/{portal_id}/tenders")
async def list_tenders(portal_id: str, changedSince: str | None = None) -> list[dict]:
    _portal_or_404(portal_id)
    now = datetime.now(timezone.utc)
    tenders = [close_if_past_deadline(t, now) for t in db.tenders_for(portal_id)]
    if changedSince is None:
        return tenders
    try:
        since = _parse_time(changedSince)
    except ValueError:
        raise HTTPException(status_code=400, detail="changedSince must be an ISO 8601 date-time")
    return [t for t in tenders if _parse_time(t["lastChangedAt"]) > since]


@app.get("/portals/{portal_id}/runs")
async def list_runs(portal_id: str) -> list[dict]:
    _portal_or_404(portal_id)
    return [
        RunOut(
            id=r["id"], kind=r["kind"], startedAt=r["started_at"],
            finishedAt=r["finished_at"], result=r["result"],
        ).model_dump()
        for r in db.runs_for(portal_id)
    ]


@app.get("/files/{file_id}")
async def get_file(file_id: str):
    doc = db.document_get(file_id)
    if doc is None or not Path(doc["path_on_disk"]).exists():
        return JSONResponse(status_code=404, content={"detail": "file not found"})
    return FileResponse(doc["path_on_disk"], filename=doc["file_name"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    costs = db.llm_cost_by_portal()
    recipes = db.recipe_history()
    beats = db.runs_by_portal()
    portals = [
        dict(row)
        | {"cost_usd": costs.get(row["id"], 0.0), "recipes": recipes.get(row["id"], []), "beats": beats.get(row["id"], [])}
        for row in db.portal_list()
    ]
    counts = {s: sum(p["state"] == s for p in portals) for s in ("serving", "integrating", "healing", "failed")}
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "portals": portals, "counts": counts, "runs": db.runs_recent(30), "total_cost": db.llm_total_cost(),
            "now": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        },
    )


@app.get("/ui/portals/{portal_id}", response_class=HTMLResponse)
async def portal_page(portal_id: str, request: Request):
    """The operator's view of one portal: its tenders, documents and every run (the JSON API stays as is)."""
    row = db.portal_get(portal_id)
    now = datetime.now(timezone.utc)
    context: dict = {"portal": None, "host": "Portal not found", "now": now.strftime("%H:%M:%S")}
    if row is None:
        return templates.TemplateResponse(request, "portal.html", context, status_code=404)
    tenders = [close_if_past_deadline(t, now) for t in db.tenders_for(portal_id)]
    # Open first, then the nearest deadline; tenders without a deadline last.
    tenders.sort(key=lambda t: (t["status"] != "open", t.get("submissionDeadline") is None,
                                _parse_time(t["submissionDeadline"]) if t.get("submissionDeadline") else now))
    runs = []
    for r in db.runs_for(portal_id, limit=100):
        took = None
        if r["finished_at"]:
            seconds = (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds()
            took = f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"
        runs.append(dict(r) | {"took": took})
    parts = row["url"].split("/", 3)
    context |= {
        "portal": dict(row),
        "host": parts[2] if len(parts) > 2 else row["url"],
        "tenders": tenders,
        "open_count": sum(t["status"] == "open" for t in tenders),
        "doc_count": sum(len(t["documents"]) for t in tenders),
        "runs": runs,
        "recipes": db.recipe_history().get(portal_id, []),
        "cost": db.llm_cost_by_portal().get(portal_id, 0.0),
    }
    return templates.TemplateResponse(request, "portal.html", context)


def _parse_time(value: str) -> datetime:
    # An unencoded "+01:00" in a query string arrives as " 01:00"; accept both.
    value = value.strip().replace(" ", "+").replace("Z", "+00:00")
    if "T" not in value and value.count("+") == 1 and len(value) > 10:
        value = value.replace("+", "T", 1)  # "2026-11-20 10:00" style
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
