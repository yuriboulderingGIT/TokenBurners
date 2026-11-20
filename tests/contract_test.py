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
