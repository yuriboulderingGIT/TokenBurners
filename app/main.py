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

from app.db import db
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


templates.env.filters["ago"] = _ago


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
    tenders = db.tenders_for(portal_id)
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


def _parse_time(value: str) -> datetime:
    # An unencoded "+01:00" in a query string arrives as " 01:00"; accept both.
    value = value.strip().replace(" ", "+").replace("Z", "+00:00")
    if "T" not in value and value.count("+") == 1 and len(value) > 10:
        value = value.replace("+", "T", 1)  # "2026-11-20 10:00" style
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
