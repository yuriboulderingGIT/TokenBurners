"""Measure element boxes (in 3x screenshot pixels) on the live demo pages, same viewport as the captures.

Writes OUT/boxes.json: {view: {name: [x, y, w, h]}}.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from _paths import REPO  # noqa: F401,E402  (sets PLAYWRIGHT_BROWSERS_PATH)
from playwright.async_api import async_playwright  # noqa: E402

OUT = Path(sys.argv[1])
portal_id = (OUT / "portal_id.txt").read_text(encoding="utf-8").strip()
S = 3


async def box(page, selector, index=0):
    loc = page.locator(selector)
    if await loc.count() <= index:
        return None
    b = await loc.nth(index).bounding_box()
    return [round(b["x"] * S), round(b["y"] * S), round(b["width"] * S), round(b["height"] * S)] if b else None


async def main() -> None:
    boxes = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=S)

        await page.goto("http://localhost:8080/")
        boxes["dashboard"] = {
            "field": await box(page, "#add-portal input"),
            "add": await box(page, "#add-portal button"),
            "h1": await box(page, "main h1"),
            "kpis": await box(page, "section.kpis"),
            "card": await box(page, "article.card"),
            "activity": await box(page, "aside, .activity, section.activity"),
        }

        await page.goto(f"http://localhost:8080/ui/portals/{portal_id}")
        facts = page.locator("div.facts")
        boxes["portal"] = {
            "title": await box(page, "div.title"),
            "pill": await box(page, "div.title .pill"),
            "url": await box(page, "div.facts", 0),
            "reason": await box(page, "div.facts", 1),
            "meta": await box(page, "div.facts", 2),
            "actions": await box(page, "div.actions"),
            "table": await box(page, "table"),
            "tbody": await box(page, "table tbody"),
        }
        spans = facts.nth(2).locator("span")
        for i in range(await spans.count()):
            text = (await spans.nth(i).inner_text()).split(" ")[0].lower()
            b = await spans.nth(i).bounding_box()
            boxes["portal"][f"meta-{text}"] = [round(b["x"] * S), round(b["y"] * S), round(b["width"] * S), round(b["height"] * S)]
        boxes["portal"]["ids-col"] = await box(page, "table tbody tr td:nth-child(2)")
        rows = page.locator("table tbody tr")
        first = await rows.first.bounding_box()
        last = await rows.last.bounding_box()
        boxes["portal"]["rows"] = [round(first["x"] * S), round(first["y"] * S), round(first["width"] * S),
                                   round((last["y"] + last["height"] - first["y"]) * S)]

        row = page.locator("tr", has_text="MOCK-003")
        await row.locator("summary").first.click()
        await page.wait_for_timeout(300)
        await row.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 140)")
        await page.wait_for_timeout(400)
        boxes["docs-open"] = {
            "row": await box(page, "tr:has-text('MOCK-003')"),
            "files": await box(page, "tr:has-text('MOCK-003') ul.docs"),
            "deadline": await box(page, "tr:has-text('MOCK-003') td:nth-child(5)"),
            "deadline-col": await box(page, "table thead th:nth-child(5)"),
        }
        await browser.close()
    (OUT / "boxes.json").write_text(json.dumps(boxes, indent=1), encoding="utf-8")
    print(json.dumps(boxes))


asyncio.run(main())
