import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.fetcher import Fetcher
from app.models import Locale, RawTender, Recipe
from brain.learn import AttachFailed, learn_recipe
from brain.normalize import normalize
from brain.run_recipe import RecipeReadError, run_recipe
from mock_portal import server


@pytest.mark.parametrize(("host", "country", "expected"), [
    ("portal.de", None, "+02:00"), ("portal.fr", None, "+02:00"),
    ("portal.nl", None, "+02:00"), ("portal.be", None, "+02:00"),
    ("portal.gov.ie", None, "+01:00"), ("portal.gov.uk", None, "+01:00"),
    ("portal.de", "IE", "+01:00"), ("portal.com", "de", "+02:00"),
])
def test_deadline_timezone_fallback(host, country, expected):
    raw = RawTender(url=f"https://{host}/notice", fields={
        "id": "T", "submissionDeadline": "09/06/2026 10:30",
    })
    tender = normalize(raw, Locale(country=country))
    assert tender.submissionDeadline == "2026-06-09T10:30:00" + expected


def test_explicit_timezone_precedes_country():
    raw = RawTender(url="https://x.fr", fields={"id": "T", "submissionDeadline": "2026-07-10T10:30"})
    assert normalize(raw, Locale(tz="UTC", country="BE")).submissionDeadline.endswith("+00:00")


@pytest.fixture
def mock(monkeypatch):
    monkeypatch.setattr(server, "STATE", server.DEFAULT_STATE.copy())
    with TestClient(server.app, base_url="http://mock.test") as client:
        yield client


def test_switches_and_reset(mock):
    mock.get("/_switch", params={"dead": True, "moved": True, "https": True})
    assert mock.get("/tender/MOCK-005").status_code == 404
    assert mock.get("/tender/MOCK-001").status_code == 200
    assert 'href="/notices"' in mock.get("/").text
    assert mock.get("/tenders").status_code == 404
    response = mock.get("/notices?page=2", follow_redirects=False)
    assert response.headers["location"] == "https://mock.test/notices?page=2"
    assert "MOCK-005" in mock.get(response.headers["location"]).text
    assert '/notices?page=2' in mock.get("/notices").text
    mock.get("/_switch?reset=1")
    assert mock.get("/notices").status_code == 404
    assert mock.get("/tender/MOCK-005").status_code == 200
    assert mock.get("/tenders", follow_redirects=False).status_code == 200


def _recipe(url):
    recipe = Recipe.model_validate_json(
        (Path(__file__).parents[1] / "testset/fixtures/mock/recipe_v1.json").read_text()
    )
    recipe.list_.url = url
    return recipe


def test_mock_dead_detail_still_reads_four_tenders(mock):
    mock.get("/_switch?dead=true")

    async def run():
        f = Fetcher(min_interval=0, transport=httpx.ASGITransport(app=server.app))
        try:
            return await run_recipe(_recipe("http://mock.test/tenders"), f.for_portal("mock"))
        finally:
            await f.aclose()

    try:
        rows = asyncio.run(run())
    except RecipeReadError as exc:
        if "404" in str(exc):
            pytest.xfail("B11 still treats a missing detail page as a broken recipe")
        raise
    assert {row.fields["id"] for row in rows} == {t["id"] for t in server.TENDERS[:-1]}


def test_mock_https_upgrade_attaches_on_final_origin(mock):
    mock.get("/_switch?https=true")

    class RecipeModel:
        model_name = "fixture"

        async def json(self, prompt, **kwargs):
            evidence = json.loads(prompt)
            if "entry" in evidence:
                return {"list_url": evidence["entry"]["url"], "detail_url": "/tender/MOCK-001"}
            return _recipe(evidence["list_page"]["url"]).model_dump(by_alias=True)

    async def attach():
        f = Fetcher(min_interval=0, transport=httpx.ASGITransport(app=server.app))
        try:
            return await learn_recipe("http://mock.test/tenders", f.for_portal("mock"), RecipeModel())
        finally:
            await f.aclose()

    try:
        recipe = asyncio.run(attach())
    except (AttachFailed, RecipeReadError) as exc:
        if "redirected outside" in str(exc):
            pytest.xfail("B12 still rejects an HTTP to HTTPS upgrade")
        raise
    assert recipe.list_.url == "https://mock.test/tenders"


def test_greek_portal_uses_english_server_dates():
    from lxml import html

    page = html.parse(str(Path(__file__).parents[1] / "testset/fixtures/etenders-cy/list.html"))
    row = page.xpath("//table/tbody/tr")[0]
    raw = RawTender(url="https://www.eprocurement.gov.cy/epps/", fields={
        "id": row.xpath("string(td[3])"),
        "publicationDate": row.xpath("string(td[6])"),
        "submissionDeadline": row.xpath("string(td[7])"),
    })
    tender = normalize(raw, Locale(lang="el", country="CY"))
    assert tender.publicationDate == "2026-11-20"
    assert tender.submissionDeadline == "2026-12-21T11:00:00+02:00"


def test_domain_fallback_preserves_explicit_date_order():
    raw = RawTender(url="https://x.de", fields={"id": "T", "submissionDeadline": "12/09/2026 10:30"})
    assert normalize(raw, Locale(lang="en-US")).submissionDeadline == "2026-12-09T10:30:00+01:00"
