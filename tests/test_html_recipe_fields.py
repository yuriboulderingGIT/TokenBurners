"""The HTML recipe prompt names the fields that a portal can supply."""

import asyncio
import json

from app.db import DB
from app.fetcher import FakeFetcher
from app.models import OPTIONAL_TENDER_FIELDS
from app.worker import Worker


def test_attach_maps_visible_notice_fields_and_leaves_sparse_notice_fields_absent(tmp_path):
    url = "https://portal.example/tenders"
    listing = """
    <main lang="en"><p>All deadlines use Europe/Brussels time.</p>
      <article data-id="B-17"><h2><a href="/notice/17">School roof</a></h2><dl>
        <dt>Authority</dt><dd>City of Liège</dd>
        <dt>Published</dt><dd>2026-05-08</dd>
        <dt>Due</dt><dd>2026-07-01 12:00</dd>
        <dt>Notice status</dt><dd>Cancelled</dd>
        <dt>Reference</dt><dd>ROOF/26</dd>
        <dt>Summary</dt><dd>Replace the roof of the municipal school.</dd>
        <dt>Procedure</dt><dd>Open procedure</dd>
        <dt>Category</dt><dd>Works</dd>
        <dt>CPV</dt><dd>45261900-3</dd>
        <dt>Work postcode</dt><dd>4000</dd>
        <dt>Work city</dt><dd>Liège</dd>
        <dt>Work country</dt><dd>BE</dd>
      </dl></article>
      <article data-id="B-18"><h2><a href="/notice/18">School desks</a></h2></article>
    </main>"""
    labels = {
        "buyerName": "Authority", "publicationDate": "Published", "submissionDeadline": "Due",
        "status": "Notice status", "referenceNumber": "Reference", "description": "Summary",
        "procedureType": "Procedure", "contractNature": "Category", "cpvCodes": "CPV",
        "placeOfPerformance.postalCode": "Work postcode", "placeOfPerformance.city": "Work city",
        "placeOfPerformance.countryCode": "Work country",
    }

    class FixtureLLM:
        model_name = "fixture-mini"
        total_cost = 0

        def scoped(self, *_args):
            return self

        async def json(self, prompt, *, system, strong=False):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                return {"list_url": url, "detail_url": None}
            task = evidence["task"]
            for key in ["fields.id", "fields.url", "title", *labels]:
                assert key in task
            assert "IANA time zone" in task and "locale.tz to null" in task
            return {
                "list": {"url": url, "item": "//article"},
                "fields": {
                    "id": "./@data-id", "title": "./h2/a", "url": "./h2/a/@href",
                    **{key: f".//dt[.='{label}']/following-sibling::dd[1]" for key, label in labels.items()},
                },
                "locale": {"tz": "Europe/Brussels", "lang": "en"},
            }

    db = DB(tmp_path / "notices.sqlite")
    portal_id = db.portal_create(url)

    async def attach():
        fetcher = FakeFetcher({url: listing})
        worker = Worker(db, fetcher, FixtureLLM())
        try:
            await worker.start()
            worker.request_read(portal_id)
            await asyncio.wait_for(worker.queue.join(), timeout=5)
        finally:
            await worker.stop()
            await fetcher.aclose()

    asyncio.run(attach())
    portal = db.portal_get(portal_id)
    assert portal["state"] == "serving", portal["reason"]
    tenders = {t["id"]: t for t in db.tenders_for(portal_id)}
    full = tenders["B-17"]
    assert {key: full[key] for key in OPTIONAL_TENDER_FIELDS} == {
        "referenceNumber": "ROOF/26", "description": "Replace the roof of the municipal school.",
        "procedureType": "open", "contractNature": "works", "cpvCodes": ["45261900"],
        "placeOfPerformance": {"postalCode": "4000", "city": "Liège", "countryCode": "BE"},
    }
    assert full["url"] == "https://portal.example/notice/17"
    assert full["status"] == "withdrawn"
    assert full["submissionDeadline"] == "2026-07-01T12:00:00+02:00"
    assert set(OPTIONAL_TENDER_FIELDS).isdisjoint(tenders["B-18"])
