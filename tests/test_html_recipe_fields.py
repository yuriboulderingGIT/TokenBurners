"""The HTML recipe prompt names the fields that a portal can supply."""

import asyncio
import json

from app.db import DB
from app.fetcher import FakeFetcher
from app.models import OPTIONAL_TENDER_FIELDS
from app.worker import Worker
from brain.learn import learn_recipe
from brain.run_recipe import run_recipe


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
      </dl><a class="doc" href="/files/roof.pdf">Tender documents</a></article>
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
                "documents": {"link": ".//a[@class='doc']/@href"},
                "locale": {"tz": "Europe/Brussels", "lang": "en"},
            }

    db = DB(tmp_path / "notices.sqlite")
    portal_id = db.portal_create(url)

    async def attach():
        fetcher = FakeFetcher({url: listing, "https://portal.example/files/roof.pdf": b"%PDF-1.4 roof"})
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


def test_attach_recovers_full_listing_null_field_and_missed_next_link():
    entry = "https://portal.example/market"
    listing = "https://portal.example/search?country=DE"
    page_two = listing + "&page=2"
    pages = {
        entry: f'<a href="{listing}">View all tenders</a><article>Market highlights</article>',
        listing: '<article data-id="1">School roof</article>'
                 '<a aria-label="Next page" href="?country=DE&amp;page=2">→</a>',
        page_two: '<article data-id="2">School desks</article>'
                  '<a aria-label="Next page" aria-disabled="true" href="?page=3">→</a>',
    }
    proposals = []

    class Model:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                return {"list_url": entry}
            url = evidence["list_page"]["url"]
            proposals.append(url)
            return {"list": {"url": url, "item": "//article", "next_page": "//a[@class='more']/@href"},
                    "fields": {"id": "@data-id", "title": "text()", "placeOfPerformance.countryCode": None}}

    async def attach_and_read():
        fetcher = FakeFetcher(pages)
        try:
            fetch = fetcher.for_portal("fixture")
            recipe = await learn_recipe(entry, fetch, Model())
            assert recipe.list_.url == listing
            assert "placeOfPerformance.countryCode" not in recipe.fields
            assert [row.fields["id"] for row in await run_recipe(recipe, fetch)] == ["1", "2"]
        finally:
            await fetcher.aclose()

    asyncio.run(attach_and_read())
    assert proposals == [entry, listing]
