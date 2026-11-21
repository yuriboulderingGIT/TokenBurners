"""Upgrade the legacy category snapshots without exposing them as real notices."""

import asyncio
import json
import sqlite3

from app import main
from app.db import DB, SCHEMA
from app.fetcher import FakeFetcher
from app.models import Recipe, Tender
from app.worker import Worker


BASE = "https://portal.example"
AT = "2026-01-01T12:00:00+00:00"


def notice(ident, url, **updates):
    return Tender(id=ident, url=url, title="School roof", buyerName=None, submissionDeadline=None,
                  publicationDate=None, status="open", lastChangedAt=AT).model_copy(update=updates)


def legacy_db(path, rows):
    # Create the database before the tender lifecycle and category archive upgrades.
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO portals(id,url,state,reason,tender_count,created_at,last_success_at) "
                     "VALUES('p',?,'serving',?,?,?,?)", (BASE, f"delivering {len(rows)} tenders", len(rows), AT, AT))
        conn.executemany("INSERT INTO tenders(portal_id,id,json,hash,last_changed_at,last_seen_at,status) "
                         "VALUES('p',?,?,?,?,?,?)",
                         [(row.id, json.dumps(row.api_dict()), "original digest", AT, AT, row.status) for row in rows])


def test_directory_only_snapshot_stays_archived_across_repair_and_restart(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    rows = [notice(str(i), f"{BASE}/company/categoryOverview.do?code={i}") for i in range(44)]
    legacy_db(path, rows)
    db = DB(path)
    monkeypatch.setattr(main, "db", db)
    assert db.tenders_listed_last("p") == [] and db.unlisted_active_details("p", set()) == []
    assert asyncio.run(main.list_tenders("p")) == []
    before = asyncio.run(main.get_portal("p"))
    assert (before["state"], before["tenderCount"], before["lastSuccessAt"]) == ("failed", 0, None)

    async def repair():
        recipe = Recipe.model_validate({"kind": "json_api", "list": {"url": BASE, "item": "$.notices[*]"},
                                        "fields": {"id": "$.id", "title": "$.title"}})
        fetcher = FakeFetcher({BASE: json.dumps({"notices": [{"id": "3", "title": "Bridge"}]})})
        try:
            worker = Worker(db, fetcher, None)
            assert await worker._read("p", db.run_create("p", "read"), allow_repair=False, repaired_recipe=recipe)
        finally:
            await fetcher.aclose()
    asyncio.run(repair())
    reopened = DB(path)
    monkeypatch.setattr(main, "db", reopened)
    result = asyncio.run(main.get_portal("p"))
    assert (result["state"], result["tenderCount"]) == ("serving", 1)
    assert [(t["id"], t["title"]) for t in asyncio.run(main.list_tenders("p"))] == [("3", "Bridge")]
    archived = reopened.all("SELECT id,json,hash,last_seen_at FROM legacy_category_rows ORDER BY CAST(id AS INT)")
    assert [(row["id"], json.loads(row["json"]), row["hash"], row["last_seen_at"]) for row in archived] == [
        (row.id, row.api_dict(), "original digest", AT) for row in rows
    ]


def test_upgrade_preserves_real_notices_and_ambiguous_category_listing_urls(tmp_path):
    path = tmp_path / "mixed.db"
    shared = f"{BASE}/categoryOverview.do?code=45"
    genuine = [
        notice("1", f"{BASE}/notice?return=/categoryOverview.do"),
        notice("2", f"{BASE}/categoryOverview.do/individual?code=7"),
        notice("3", f"{BASE}/categoryOverview.do?code=105", buyerName="City"),
        notice("4", shared), notice("5", shared),
    ]
    legacy_db(path, genuine + [notice("6", f"{BASE}/categoryOverview.do?code=61")])
    db = DB(path)
    assert [t["id"] for t in db.tenders_for("p")] == [row.id for row in genuine]
    assert [row["id"] for row in db.all("SELECT id FROM legacy_category_rows")] == ["6"]
    portal = db.portal_get("p")
    assert (portal["state"], portal["tender_count"], portal["last_success_at"]) == ("serving", 5, AT)
