"""Free extra stills of the portal page (no model calls): opened documents and the runs tab.

Usage: python views.py OUT_DIR SUBFOLDER
"""

import asyncio
import os
import sys
from pathlib import Path

from _paths import REPO  # noqa: F401,E402  (sets PLAYWRIGHT_BROWSERS_PATH)
from playwright.async_api import async_playwright  # noqa: E402

OUT = Path(sys.argv[1])
folder = OUT / sys.argv[2]
folder.mkdir(parents=True, exist_ok=True)
portal_id = (OUT / "portal_id.txt").read_text(encoding="utf-8").strip()


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=3)
        await page.goto(f"http://localhost:8080/ui/portals/{portal_id}")
        row = page.locator("tr", has_text="MOCK-003")
        summary = row.locator("summary")
        if await summary.count():
            await summary.first.click()
        else:
            await row.get_by_text("4 files").first.click()
        await page.wait_for_timeout(300)
        await row.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 140)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(folder / "docs-open.png"))
        await page.screenshot(path=str(folder / "docs-open-full.png"), full_page=True)
        await page.goto(f"http://localhost:8080/ui/portals/{portal_id}")
        await page.locator("button[data-tab=runs]").click()
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(folder / "runs.png"))
        first = page.locator("details summary >> visible=true").first
        if await first.count():
            await first.click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(folder / "runs-open-full.png"), full_page=True)
        await browser.close()
        print("done")


asyncio.run(main())
