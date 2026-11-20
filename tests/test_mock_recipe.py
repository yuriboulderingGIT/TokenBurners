"""The hand-written recipe matches the mock portal (v1) and breaks on layout v2 (for repair tests)."""

import json
from pathlib import Path

from lxml import html

from app.models import Recipe
from mock_portal.server import render_detail, render_list

RECIPE = Recipe.model_validate(
    json.loads((Path(__file__).parents[1] / "testset/fixtures/mock/recipe_v1.json").read_text(encoding="utf-8"))
)


def _text(doc, xpath: str) -> str:
    found = doc.xpath(xpath)
    assert found, f"nothing found for {xpath}"
    item = found[0]
    return item if isinstance(item, str) else item.text_content().strip()


def test_recipe_reads_mock_v1() -> None:
    page = html.fromstring(render_list(1, "v1"))
    items = page.xpath(RECIPE.list_.item)
    assert len(items) == 3
    assert items[0].xpath(RECIPE.detail.link)[0] == "/tender/MOCK-001"
    assert page.xpath(RECIPE.list_.next_page)[0] == "/tenders?page=2"

    detail = html.fromstring(render_detail("MOCK-001", "v1"))
    assert _text(detail, RECIPE.fields["id"]) == "MOCK-001"
    assert _text(detail, RECIPE.fields["title"]) == "Road resurfacing, Main Street"
    assert _text(detail, RECIPE.fields["buyerName"]) == "City of Sampletown"
    assert _text(detail, RECIPE.fields["publicationDate"]) == "20 Nov 2026"
    assert _text(detail, RECIPE.fields["submissionDeadline"]) == "09 Dec 2026, 10:00"
    assert _text(detail, RECIPE.fields["referenceNumber"]) == "PR-2026-001"
    assert _text(detail, RECIPE.fields["cpvCodes"]) == "45233140-2"
    assert _text(detail, RECIPE.documents.link) == "/download/MOCK-001/tender-documents.pdf"


def test_layout_v2_breaks_recipe_v1() -> None:
    page = html.fromstring(render_list(1, "v2"))
    assert page.xpath(RECIPE.list_.item) == []
    detail = html.fromstring(render_detail("MOCK-001", "v2"))
    assert detail.xpath(RECIPE.fields["id"]) == []
