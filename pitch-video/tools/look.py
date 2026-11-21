"""Screenshot pages at 1280x720 CSS pixels x3 = 3840x2160 (a 1920x1080 screen at 150 % zoom, in 4K).

Usage: python look.py OUT_DIR name=url [name=url ...]
Also writes a 960 px wide preview next to each PNG for quick viewing.
"""

import asyncio
import os
import sys
from pathlib import Path

from _paths import REPO  # noqa: F401,E402  (sets PLAYWRIGHT_BROWSERS_PATH)
from playwright.async_api import async_playwright  # noqa: E402


async def main(out: Path, pairs: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=3)
        for pair in pairs:
            name, url = pair.split("=", 1)
            await page.goto(url, wait_until="networkidle")
            await page.screenshot(path=str(out / f"{name}.png"))
            small = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=0.75)
            await small.goto(url, wait_until="networkidle")
            await small.screenshot(path=str(out / f"{name}-preview.png"), full_page=True)
            await small.close()
            print("saved", name, flush=True)
        await browser.close()


asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
