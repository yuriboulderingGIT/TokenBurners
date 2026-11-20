"""Calls every endpoint and checks status codes and JSON shapes (plan section 3). OWNER: A1.

Run before every merge to main:  pytest
"""

import time
from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app

PORTAL_KEYS = {"id", "url", "state", "reason", "tenderCount", "lastSuccessAt"}
RUN_KEYS = {"id", "kind", "startedAt", "finishedAt", "result"}
TENDER_REQUIRED = {
    "id", "url", "title", "buyerName", "publicationDate", "submissionDeadline",
    "status", "documents", "lastChangedAt",
}
TENDER_OPTIONAL = {
    "referenceNumber", "description", "procedureType", "contractNature", "cpvCodes", "placeOfPerformance",
}
STATES = {"integrating", "serving", "healing", "failed"}


def has_offset(value: str) -> bool:
    return datetime.fromisoformat(value).tzinfo is not None


def wait_until_settled(client: TestClient, portal_id: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        portal = client.get(f"/portals/{portal_id}").json()
        if portal["state"] != "integrating":
            return portal
        time.sleep(0.1)
    raise AssertionError("portal stayed in integrating")


def test_contract() -> None:
    with TestClient(app) as client:
        # POST /portals -> 201 {"id"}; same URL -> same id
        r = client.post("/portals", json={"url": "https://portal.example/contract-a"})
        assert r.status_code == 201
        assert set(r.json()) == {"id"}
        portal_id = r.json()["id"]
        assert client.post("/portals", json={"url": "https://portal.example/contract-a"}).json()["id"] == portal_id
        assert client.post("/portals", json={"url": "https://portal.example/contract-a/"}).json()["id"] == portal_id

        # GET /portals/{id}: exact shape
        portal = wait_until_settled(client, portal_id)
        assert set(portal) == PORTAL_KEYS
        assert portal["state"] in STATES
        assert isinstance(portal["tenderCount"], int)
        if portal["lastSuccessAt"] is not None:
            assert has_offset(portal["lastSuccessAt"])

        # GET /portals: a list in the same shape
        r = client.get("/portals")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert all(set(p) == PORTAL_KEYS for p in r.json())
        assert portal_id in {p["id"] for p in r.json()}

        # GET /portals/{id}/tenders
        r = client.get(f"/portals/{portal_id}/tenders")
        assert r.status_code == 200
        tenders = r.json()
        assert isinstance(tenders, list)
        assert len(tenders) == portal["tenderCount"]
        for t in tenders:
            assert TENDER_REQUIRED <= set(t), f"missing required fields: {TENDER_REQUIRED - set(t)}"
            assert set(t) <= TENDER_REQUIRED | TENDER_OPTIONAL, f"unknown fields: {set(t) - TENDER_REQUIRED - TENDER_OPTIONAL}"
            assert t["status"] in {"open", "closed", "withdrawn"}
            assert has_offset(t["lastChangedAt"])
            if t["submissionDeadline"] is not None:
                assert has_offset(t["submissionDeadline"])
            for code in t.get("cpvCodes", []):
                assert len(code) == 8 and code.isdigit()
            for doc in t["documents"]:
                assert set(doc) == {"fileName", "sizeBytes", "sha256", "path"}
            for key in TENDER_OPTIONAL:
                assert t.get(key, "absent") is not None, f"optional {key} must be left out, not null"

        # ?changedSince (both URL-encoded and raw "+" must work)
        assert client.get(f"/portals/{portal_id}/tenders", params={"changedSince": "2999-01-01T00:00:00+01:00"}).json() == []
        assert client.get(f"/portals/{portal_id}/tenders?changedSince=2999-01-01T00:00:00+00:00").json() == []
        assert len(client.get(f"/portals/{portal_id}/tenders?changedSince=2000-01-01T00:00:00+00:00").json()) == len(tenders)

        # POST refresh -> 202 {"runId"}
        r = client.post(f"/portals/{portal_id}/refresh")
        assert r.status_code == 202
        assert set(r.json()) == {"runId"}

        # GET runs: first attach counts as a read
        r = client.get(f"/portals/{portal_id}/runs")
        assert r.status_code == 200
        runs = r.json()
        assert runs and all(set(run) == RUN_KEYS for run in runs)
        assert runs[-1]["kind"] == "read"
        for run in runs:
            assert run["kind"] in {"read", "repair"}
            assert run["result"] in {"ok", "failed", None}
            assert has_offset(run["startedAt"])
            if run["finishedAt"] is not None:
                assert has_offset(run["finishedAt"])

        # Dashboard
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

        # Unknown ids -> 404
        assert client.get("/portals/nope").status_code == 404
        assert client.get("/portals/nope/tenders").status_code == 404
        assert client.get("/portals/nope/runs").status_code == 404
        assert client.post("/portals/nope/refresh").status_code == 404
        assert client.delete("/portals/nope").status_code == 404
        assert client.get("/files/nope").status_code == 404

        # DELETE -> 204, then gone
        r = client.delete(f"/portals/{portal_id}")
        assert r.status_code == 204
        assert client.get(f"/portals/{portal_id}").status_code == 404
        assert portal_id not in {p["id"] for p in client.get("/portals").json()}


def test_open_tender_past_its_deadline_is_served_closed() -> None:
    """Even when no healthy read rewrites it (portal healing or failed)."""
    import asyncio

    from app.db import db
    from app.main import list_tenders
    from app.models import Tender

    portal_id = db.portal_create("https://portal.example/deadline-passed")
    common = dict(buyerName="City", publicationDate="2026-01-01", status="open")
    db.save_tenders(portal_id, [
        Tender(id="PAST", url="https://portal.example/p", title="Past", submissionDeadline="2026-02-01T10:00:00+01:00", **common),
        Tender(id="FUTURE", url="https://portal.example/f", title="Future", submissionDeadline="2999-01-01T10:00:00+01:00", **common),
        Tender(id="NONE", url="https://portal.example/n", title="None", submissionDeadline=None, **common),
    ])
    stored = {t["id"]: t for t in db.tenders_for(portal_id)}
    served = {t["id"]: t for t in asyncio.run(list_tenders(portal_id))}

    assert served["PAST"]["status"] == "closed"
    assert served["FUTURE"]["status"] == served["NONE"]["status"] == "open"
    assert served["FUTURE"] == stored["FUTURE"]
    # Stored lastChangedAt (now) is later than the deadline, so it stays: never moves backwards.
    assert served["PAST"]["lastChangedAt"] == stored["PAST"]["lastChangedAt"]
    assert stored["PAST"]["status"] == "open"  # serve-time only, the database is untouched
    since = asyncio.run(list_tenders(portal_id, changedSince="2026-01-15T00:00:00+00:00"))
    assert {t["id"] for t in since} == {"PAST", "FUTURE", "NONE"}


def test_closing_at_deadline_sets_lastchangedat_to_the_deadline() -> None:
    from datetime import timezone

    from app.db import close_if_past_deadline

    tender = {"status": "open", "submissionDeadline": "2026-11-20T10:00:00+01:00", "lastChangedAt": "2026-11-01T08:00:00+00:00"}
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert close_if_past_deadline(tender, now) == tender | {"status": "closed", "lastChangedAt": "2026-11-20T10:00:00+01:00"}
    assert close_if_past_deadline(tender | {"status": "withdrawn"}, now)["status"] == "withdrawn"


def test_healthy_read_after_serve_time_close_is_not_a_second_change() -> None:
    import json

    from app.db import db
    from app.main import list_tenders
    from app.models import Tender
    import asyncio

    portal_id = db.portal_create("https://portal.example/no-double-change")
    fields = dict(id="T", url="https://portal.example/t", title="T", buyerName="City",
                  publicationDate="2026-01-01", submissionDeadline="2026-02-01T10:00:00+01:00")
    db.save_tenders(portal_id, [Tender(status="open", **fields)])
    # Pretend it was stored long before the deadline passed.
    row = db.one("SELECT json FROM tenders WHERE portal_id = ? AND id = 'T'", (portal_id,))
    stored = json.loads(row["json"]) | {"lastChangedAt": "2026-01-10T00:00:00+00:00"}
    db.execute("UPDATE tenders SET json = ? WHERE portal_id = ? AND id = 'T'", (json.dumps(stored), portal_id))

    served = asyncio.run(list_tenders(portal_id))[0]
    assert served["status"] == "closed" and served["lastChangedAt"] == "2026-02-01T10:00:00+01:00"
    db.save_tenders(portal_id, [Tender(status="closed", **fields)])  # the next healthy read
    assert asyncio.run(list_tenders(portal_id))[0] == served

    db.save_tenders(portal_id, [Tender(status="closed", **(fields | {"title": "T2"}))])  # a real change
    assert asyncio.run(list_tenders(portal_id))[0]["lastChangedAt"] != served["lastChangedAt"]


def test_dashboard_links_to_an_html_portal_page() -> None:
    import asyncio

    from app.db import db
    from app.main import dashboard, portal_page
    from app.models import Document, Tender

    portal_id = db.portal_create("https://portal.example/ui-page")
    db.save_tenders(portal_id, [Tender(
        id="UI-1", url="https://portal.example/ui-1", title="Bridge <repair>", buyerName="City",
        publicationDate="2026-11-20", submissionDeadline="2999-12-09T10:00:00+01:00", status="open",
        documents=[Document(fileName="spec.pdf", sizeBytes=2048, sha256="ab" * 32, path="/files/abc")],
    )])

    from starlette.requests import Request
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    page = asyncio.run(portal_page(portal_id, req))
    html = page.body.decode()
    assert page.status_code == 200
    assert "Bridge &lt;repair&gt;" in html  # escaped
    assert 'href="/files/abc"' in html and "2.0 KB" in html
    assert "09 Dec 2999, 10:00 +01:00" in html
    assert f"/ui/portals/{portal_id}" in asyncio.run(dashboard(req)).body.decode()
    assert asyncio.run(portal_page("nope", req)).status_code == 404
