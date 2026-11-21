"""A list longer than MAX_PAGES fails with a clear reason and no further model calls.

Seen live on 20/21 Nov: etenders.gov.ie lists 3,013 results on 302 pages. The model raised
max_pages to cover them, validation rejected "max_pages must be between 1 and 100", and the portal
failed with that message after several paid attempts.
"""

import asyncio
import json

import pytest

from app import states
from app.db import DB
from app.fetcher import FakeFetcher
from app.models import Recipe
from app.worker import Worker
from brain.learn import AttachFailed, learn_recipe
from brain.run_recipe import MAX_PAGES, PageLimitExceeded, PaginationError, run_recipe

URL = "https://portal.example/tenders"


def _pages(count: int) -> dict[str, str]:
    pages = {}
    for n in range(1, count + 1):
        url = URL if n == 1 else f"{URL}?page={n}"
        following = f'<a rel="next" href="{URL}?page={n + 1}">Next</a>' if n < count else ""
        pages[url] = (
            f'<table><tr data-id="T{n}"><td><a href="/notice/{n}">Roof {n}</a></td>'
            f"<td>2026-12-01 12:00</td></tr></table>{following}"
        )
    return pages


def _recipe(max_pages: int) -> dict:
    return {
        "list": {"url": URL, "item": "//tr[@data-id]", "next_page": "//a[@rel='next']/@href", "max_pages": max_pages},
        "fields": {"id": "./@data-id", "title": "./td[1]/a", "submissionDeadline": "./td[2]"},
    }


class Model:
    model_name = "fixture-mini"
    strong_model_name = "fixture-strong"
    total_cost = 0

    def __init__(self, max_pages: int) -> None:
        self.max_pages, self.calls = max_pages, 0

    def scoped(self, *_args):
        return self

    async def json(self, prompt, *, system, strong=False):
        if "entry" in json.loads(prompt):
            return {"list_url": URL, "detail_url": None}
        self.calls += 1
        return _recipe(self.max_pages)


def _run(coro_factory, pages):
    async def run():
        fetcher = FakeFetcher(pages)
        try:
            return await coro_factory(fetcher.for_portal("fixture"))
        finally:
            await fetcher.aclose()

    return asyncio.run(run())


def test_reader_names_the_hard_limit_but_keeps_ordinary_page_budget_errors():
    with pytest.raises(PageLimitExceeded, match=r"longer than the 100-page limit \(100 rows on the first 100 pages\)"):
        _run(lambda fetch: run_recipe(Recipe.model_validate(_recipe(MAX_PAGES)), fetch), _pages(MAX_PAGES + 5))
    with pytest.raises(PaginationError) as small_budget:
        _run(lambda fetch: run_recipe(Recipe.model_validate(_recipe(20)), fetch), _pages(30))
    assert not isinstance(small_budget.value, PageLimitExceeded)  # the model may still raise its own budget


def test_attach_stops_at_once_with_a_clear_reason_when_the_list_is_too_long():
    model = Model(max_pages=302)  # the model counted the pager: more than the reader allows
    with pytest.raises(AttachFailed) as failed:
        _run(lambda fetch: learn_recipe(URL, fetch, model), _pages(MAX_PAGES + 50))
    reason = str(failed.value)
    assert "longer than the 100-page limit (about 302 pages advertised" in reason
    assert "narrower list URL" in reason and "max_pages must be" not in reason
    assert model.calls == 1  # no paid retries: no recipe can make the list shorter


def test_an_overcounted_page_budget_is_capped_and_checked_on_the_real_pages():
    model = Model(max_pages=302)
    recipe = _run(lambda fetch: learn_recipe(URL, fetch, model), _pages(40))
    assert recipe.list_.max_pages == MAX_PAGES


def test_a_tenders_own_page_is_not_one_of_its_documents():
    # tenderned.nl, 21 Nov: the model mapped documents.link and fields.url to the same notice link.
    feed = "https://portal.example/api/publications?page=0&size=2"
    recipe = Recipe.model_validate({
        "kind": "json_api", "list": {"url": feed, "item": "$.content[*]"},
        "fields": {"id": "$.id", "title": "$.name", "url": "$.link.href"},
        "documents": {"link": "$.files[*].href"},
    })
    records = [
        {"id": "1", "name": "Roof", "link": {"href": "https://portal.example/notice/1"},
         "files": [{"href": "https://portal.example/notice/1"}, {"href": "https://portal.example/files/1.pdf"}]},
        {"id": "2", "name": "Desks", "link": {"href": "https://portal.example/notice/2"},
         "files": [{"href": "https://portal.example/notice/2"}]},
    ]
    rows = _run(lambda fetch: run_recipe(recipe, fetch), {feed: json.dumps({"content": records})})
    assert [row.document_links for row in rows] == [["https://portal.example/files/1.pdf"], []]


def test_a_read_that_outgrows_the_limit_keeps_the_tenders_and_never_pays_for_repair(tmp_path, monkeypatch):
    db = DB(tmp_path / "limit.sqlite")
    portal_id = db.portal_create(URL)
    db.recipe_save(portal_id, Recipe.model_validate(_recipe(MAX_PAGES)), "attach", 0.0)
    states.set_state(db, portal_id, states.SERVING, "delivering 1 tenders")

    async def no_repair(*_args, **_kwargs):
        raise AssertionError("a list that is too long must not start a paid repair")

    monkeypatch.setattr("app.worker.repair", no_repair)
    worker = Worker(db, fetcher=FakeFetcher(_pages(MAX_PAGES + 3)), llm=None)
    run_id = worker.request_read(portal_id)
    asyncio.run(worker._handle(*worker.queue.get_nowait()))
    portal = db.portal_get(portal_id)
    assert portal["state"] == states.FAILED
    assert portal["reason"].startswith("the tender list is longer than the 100-page limit")
    assert db.one("SELECT result FROM runs WHERE id = ?", (run_id,))["result"] == "failed"
