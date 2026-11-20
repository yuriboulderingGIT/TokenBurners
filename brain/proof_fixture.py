"""Canned replies used only by the B1 demo rehearsal."""

import json
from pathlib import Path


def mock_reply(evidence: dict) -> dict:
    if "entry" in evidence:
        entry = evidence["entry"]
        detail = next((url for url in entry["links"] if "/tender/" in url), None)
        return {"list_url": entry["url"], "detail_url": detail}
    if "endpoints" in evidence:
        endpoint = next(e for e in evidence["endpoints"] if e["path"] == "/api/tenders")
        return {"endpoint": endpoint["index"], "recipe": {
            "list": {"item": "$.items[*]", "next_page": "$.next"},
            "fields": {
                "id": "$.id", "title": "$.title", "url": "$.url", "buyerName": "$.buyer",
                "publicationDate": "$.published", "submissionDeadline": "$.deadline",
                "referenceNumber": "$.reference", "cpvCodes": "$.cpv",
            },
            "documents": {"link": "$.documents[*].href"},
            "locale": {"tz": "Europe/Dublin", "lang": "en", "country": "IE"},
        }}

    listing = evidence["list_page"]
    if not listing["links"]:
        return {}  # Exercise browser discovery on the empty SPA shell.
    path = Path(__file__).resolve().parents[1] / "testset/fixtures/mock/recipe_v1.json"
    recipe = json.loads(path.read_text(encoding="utf-8"))
    recipe["list"]["url"] = listing["url"]
    if evidence.get("previous_recipe"):
        recipe["list"].update(item="//div[@class='card']", next_page="//a[@class='pager-next']/@href")
        recipe["fields"] = {
            "id": "//th[.='Identifier']/following-sibling::td",
            "title": "//h2[@class='title']",
            **{key: f"//th[.='{label}']/following-sibling::td" for key, label in {
                "buyerName": "Buyer", "publicationDate": "Publication", "submissionDeadline": "Closing date",
                "referenceNumber": "Ref.", "cpvCodes": "CPV",
            }.items()},
        }
        recipe["documents"]["link"] = "//a[@class='doc']/@href"
    return recipe


class RehearsalModel:
    model_name = strong_model_name = "local-fixture"
    total_cost = 0.0

    def scoped(self, portal_id, run_id, purpose):
        return self

    async def json(self, prompt: str, *, system: str, strong: bool = False) -> dict:
        return mock_reply(json.loads(prompt))
