"""One polite page load per real portal, screenshot at 1440x900 x2. No model calls.

Usage: python portals.py OUT_DIR
"""

import asyncio
import os
import sys
from pathlib import Path

from _paths import REPO  # noqa: F401,E402  (sets PLAYWRIGHT_BROWSERS_PATH)
from playwright.async_api import async_playwright  # noqa: E402

PORTALS = {
    "ie-etenders": "https://www.etenders.gov.ie/epps/quickSearchAction.do?searchType=cftFTS&latest=true",
    "nl-tenderned": "https://www.tenderned.nl/aankondigingen/overzicht",
    "fr-place": "https://www.marches-publics.gouv.fr/entreprise",
    "de-dtvp": "https://dtvp.de/ausschreibungen/",
    "gb-sell2wales": "https://www.sell2wales.gov.wales/Search/Search_MainPage.aspx",
    "cy-eprocurement": "https://www.eprocurement.gov.cy/epps/quickSearchAction.do?searchType=cftFTS&latest=true",
    "de-nrw": "https://www.evergabe.nrw.de/VMPCenter/company/welcome.do",
    "mt-etenders": "https://www.etenders.gov.mt/epps/quickSearchAction.do?searchType=cftFTS&latest=true",
}
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, url in PORTALS.items():
            page = await browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="en-GB")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)  # let JS apps draw their list
                await page.screenshot(path=str(OUT / f"{name}.png"))
                print("ok", name, flush=True)
            except Exception as exc:
                print("fail", name, exc.__class__.__name__, str(exc)[:120], flush=True)
            await page.close()
        await browser.close()


asyncio.run(main())
