"""Recipe executor: list -> pagination -> detail pages -> document links. OWNER: B2.

Regular reads run here and make NO model calls.

STUB: returns 3 canned tenders without touching the network, so Team Platform can build
the whole pipeline before the real executor exists. Replace by running the recipe:
fetch.get(list.url) -> XPath list.item -> follow detail.link -> evaluate fields ->
collect documents.link -> follow next_page up to max_pages. Use brain.blocks.detect_block()
on each page and raise Blocked when a login or captcha stops us.
"""

from app.fetcher import PortalFetcher
from app.models import Recipe, RawTender


async def run_recipe(recipe: Recipe, fetch: PortalFetcher) -> list[RawTender]:
    # TODO(B2, B3): real executor (html with lxml/XPath, json_api with JSON paths).
    base = recipe.list_.url
    return [
        RawTender(
            url=f"{base}#stub-{n}",
            fields={
                "id": f"STUB-{n}",
                "title": f"[stub] Example tender {n}",
                "buyerName": "Stub City Council",
                "publicationDate": "2026-11-20",
                "submissionDeadline": "2026-12-09T10:00:00+01:00",
                "referenceNumber": f"REF-{n}",
                "cpvCodes": ["45000000-7"],
            },
        )
        for n in (1, 2, 3)
    ]
