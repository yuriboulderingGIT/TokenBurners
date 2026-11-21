"""Attach must deliver each tender's documents, or keep the tenders and say what is missing.

Seen live on 20 Nov: recipes with detail.link = null and documents.link = null passed attach,
so portals reported serving with 0 documents. Attach now asks the model once more (cheap
model only) when the pages show file links the recipe missed, and otherwise keeps the recipe.
"""

import asyncio
import json

import pytest

from app.fetcher import FakeFetcher
from app.models import Recipe
from brain.learn import InvalidListingSample, learn_recipe, validate_candidate
from brain.run_recipe import run_recipe

URL = "https://portal.example/tenders"
LIST_ONLY = {
    "list": {"url": URL, "item": "//tr[@data-id]"},
    "fields": {"id": "./@data-id", "title": "./td[1]/a", "submissionDeadline": "./td[2]"},
}
WITH_DOCUMENTS = {
    **LIST_ONLY,
    "detail": {"link": "./td[1]/a/@href"},
    "fields": {"id": "//h1/@data-id", "title": "//h1", "submissionDeadline": "//dd"},
    "documents": {"link": "//a[contains(@href, '.pdf')]/@href"},
}


def _listing() -> str:
    rows = "".join(
        f'<tr data-id="T{i}"><td><a href="/notice/{i}">Roof {i}</a></td><td>2026-12-0{i} 12:00</td></tr>'
        for i in (1, 2, 3)
    )
    return f"<main><table>{rows}</table></main>"


def _pages(with_files: bool) -> dict[str, str | bytes]:
    pages: dict[str, str | bytes] = {URL: _listing()}
    for i in (1, 2, 3):
        files = f'<a href="/docs/roof-{i}.pdf">Tender documents</a>' if with_files else "<p>No files.</p>"
        pages[f"https://portal.example/notice/{i}"] = (
            f'<h1 data-id="T{i}">Roof {i}</h1><dl><dt>Due</dt><dd>2026-12-0{i} 12:00</dd></dl>{files}'
        )
        pages[f"https://portal.example/docs/roof-{i}.pdf"] = b"%PDF-1.4 roof"
    return pages


class Model:
    model_name = "fixture-mini"
    strong_model_name = "fixture-strong"
    total_cost = 0

    def __init__(self, proposals: list[dict]) -> None:
        self.proposals = proposals
        self.calls: list[tuple[bool, list[str]]] = []  # (strong, feedback) per recipe proposal

    def scoped(self, *_args):
        return self

    async def json(self, prompt, *, system, strong=False):
        evidence = json.loads(prompt)
        if "entry" in evidence:
            return {"list_url": URL, "detail_url": None}
        self.calls.append((strong, list(evidence["feedback"])))
        return self.proposals[min(len(self.calls), len(self.proposals)) - 1]


def _attach(pages: dict, model: Model):
    async def run():
        fetcher = FakeFetcher(pages)
        try:
            fetch = fetcher.for_portal("fixture")
            recipe = await learn_recipe(URL, fetch, model)
            return recipe, await run_recipe(recipe, fetch)
        finally:
            await fetcher.aclose()

    return asyncio.run(run())


def test_missed_visible_documents_get_one_cheap_retry_with_feedback():
    model = Model([LIST_ONLY, WITH_DOCUMENTS])
    recipe, rows = _attach(_pages(with_files=True), model)
    assert recipe.detail.link and recipe.documents.link
    assert all(row.document_links for row in rows)
    assert [strong for strong, _ in model.calls] == [False, False]
    assert "no sampled tender has document links" in model.calls[1][1][0]


def test_no_visible_files_keeps_the_recipe_without_another_model_call():
    model = Model([LIST_ONLY])
    recipe, rows = _attach(_pages(with_files=False), model)
    assert recipe.detail.link is None and len(rows) == 3
    assert len(model.calls) == 1  # nothing on the pages could make a second attempt better


FEED = "https://portal.example/api/notices"
FEED_RECIPE = Recipe.model_validate({
    "kind": "json_api", "list": {"url": FEED, "item": "$.items[*]"},
    "fields": {"id": "$.tenderId", "title": "$.subject", "submissionDeadline": "$.due"},
})


def _validate_feed(items: list[dict]) -> None:
    async def run():
        fetcher = FakeFetcher({FEED: json.dumps({"items": items})})
        try:
            await validate_candidate(FEED_RECIPE, fetcher.for_portal("feed"))
        finally:
            await fetcher.aclose()

    asyncio.run(run())


def _notice(n: int) -> dict:
    return {"tenderId": f"N{n}", "subject": f"Roads {n}", "due": "2026-12-07T09:00:00Z"}


def test_sample_skips_a_record_of_another_kind_in_a_mixed_feed():
    # Seen on ezamowienia.gov.pl: a procurement plan notice (no tender ID, no title) among the newest.
    _validate_feed([_notice(1), {"tenderId": None, "subject": None, "noticeType": "TenderPlanNotice"}, _notice(2)])


@pytest.mark.parametrize("items", [
    [_notice(1), {"tenderId": None, "subject": None}, {"tenderId": None, "subject": None}],  # mostly not tenders
    [_notice(1), {"tenderId": "N2", "subject": None}, _notice(3)],  # an ID without a title: a broken selector
])
def test_sample_still_rejects_mostly_blank_rows_and_half_read_tenders(items):
    with pytest.raises(InvalidListingSample):
        _validate_feed(items)


def test_retry_that_still_misses_documents_keeps_the_working_recipe_and_never_escalates():
    model = Model([LIST_ONLY, LIST_ONLY, WITH_DOCUMENTS])
    recipe, rows = _attach(_pages(with_files=True), model)
    assert recipe.documents.link is None and [row.fields["id"] for row in rows] == ["T1", "T2", "T3"]
    assert [strong for strong, _ in model.calls] == [False, False]  # at most one extra, cheap call
