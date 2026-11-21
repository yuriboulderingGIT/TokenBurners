"""Drive the Watchtower demo and capture 4K stills and bursts for the pitch video.

Viewport 1280x720 CSS px at 3x = 3840x2160 (a 1920x1080 screen at 150 % zoom, in 4K).
Stages (each a separate run, so the stills can be checked in between):
  attach   reset the mock, type the address on the dashboard, add, burst until settled
  repair   mock layout v1 -> v2, "Read again now", burst until healed
  captcha  mock captcha, "Read again now", burst until failed, reset the mock
Stills go to OUT/<stage>/, bursts to OUT/<stage>/burst/NNNN.jpg with a times.txt (seconds since start).
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from _paths import REPO  # noqa: F401,E402  (sets PLAYWRIGHT_BROWSERS_PATH)
from playwright.async_api import async_playwright  # noqa: E402

APP = "http://localhost:8080"
MOCK = "http://localhost:8090"
PORTAL_URL = f"{MOCK}/tenders"
OUT = Path(sys.argv[1])
STAGE = sys.argv[2]
STATE_FILE = OUT / "portal_id.txt"


class Shots:
    def __init__(self, page, folder: Path) -> None:
        self.page, self.folder = page, folder
        (folder / "burst").mkdir(parents=True, exist_ok=True)
        self.n, self.t0 = 0, time.monotonic()
        self.times = open(folder / "burst" / "times.txt", "a", encoding="utf-8")

    async def still(self, name: str, full: bool = False) -> None:
        await self.page.wait_for_load_state("load")
        await self.page.screenshot(path=str(self.folder / f"{name}.png"), full_page=full)
        print("still", name, flush=True)

    async def frame(self, label: str = "") -> None:
        try:
            await self.page.wait_for_load_state("load", timeout=3000)
            await self.page.screenshot(path=str(self.folder / "burst" / f"{self.n:04d}.jpg"), type="jpeg", quality=90)
        except Exception as exc:  # a reload in the middle of a shot: skip this frame
            print("frame skipped:", exc.__class__.__name__, flush=True)
            return
        self.times.write(f"{self.n:04d} {time.monotonic() - self.t0:7.2f} {label}\n")
        self.times.flush()
        self.n += 1


async def api(page, path: str):
    res = await page.request.get(APP + path)
    return await res.json()


async def burst_until_settled(page, shots: Shots, portal_id: str, runs_before: set[str], limit: float = 420) -> list[str]:
    """Screenshot about every 1.5 s until a new run exists, all runs are finished and the state is stable."""
    seen, stable_since, start = [], None, time.monotonic()
    while time.monotonic() - start < limit:
        portal = await api(page, f"/portals/{portal_id}")
        runs = await api(page, f"/portals/{portal_id}/runs")
        state = portal["state"]
        if not seen or seen[-1] != state:
            seen.append(state)
            print(f"{time.monotonic() - start:6.1f}s state={state} reason={portal['reason']!r}", flush=True)
        await shots.frame(state)
        new = [r for r in runs if r["id"] not in runs_before]
        done = new and all(r["finishedAt"] for r in runs) and state not in ("integrating", "healing")
        if done:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since > 4:
                break
        else:
            stable_since = None
        await asyncio.sleep(1.0)
    return seen


async def main() -> None:
    folder = OUT / STAGE
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=3)
        mock = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=3)
        shots = Shots(page, folder)

        if STAGE == "attach":
            await mock.goto(f"{MOCK}/_switch?reset=1")
            await mock.goto(PORTAL_URL)
            await mock.screenshot(path=str(folder / "mock-v1.png"))
            await page.goto(APP + "/")
            await shots.still("01-dashboard-empty")
            field = page.locator("#add-portal input[name=url]")
            await field.click()
            await shots.frame("typing")
            for ch in PORTAL_URL:
                await page.keyboard.type(ch)
                await shots.frame("typing")
            await shots.still("02-dashboard-typed")
            await page.locator("#add-portal button").click()
            await page.wait_for_url("**/ui/portals/**")
            portal_id = page.url.rstrip("/").split("/")[-1]
            STATE_FILE.write_text(portal_id, encoding="utf-8")
            print("portal", portal_id, flush=True)
            seen = await burst_until_settled(page, shots, portal_id, set())
            await page.reload()
            await shots.still("03-portal-after")
            await shots.still("03-portal-after-full", full=True)
            await page.goto(APP + "/")
            await shots.still("04-dashboard-after")

        else:
            portal_id = STATE_FILE.read_text(encoding="utf-8").strip()
            runs_before = {r["id"] for r in await api(page, f"/portals/{portal_id}/runs")}
            await page.goto(f"{APP}/ui/portals/{portal_id}")
            await shots.still("01-portal-before")
            if STAGE == "repair":
                await mock.goto(PORTAL_URL)
                await mock.screenshot(path=str(folder / "mock-v1.png"))
                await mock.goto(f"{MOCK}/_switch?layout=v2")
                await mock.goto(PORTAL_URL)
                await mock.screenshot(path=str(folder / "mock-v2.png"))
                await mock.screenshot(path=str(folder / "mock-v2-full.png"), full_page=True)
            else:
                await mock.goto(f"{MOCK}/_switch?variant=captcha")
                await mock.goto(PORTAL_URL)
                await mock.screenshot(path=str(folder / "mock-captcha.png"))
            await shots.frame("before")
            await page.locator("button[data-action=refresh]").click()
            seen = await burst_until_settled(page, shots, portal_id, runs_before)
            await page.reload()
            await shots.still("02-portal-after")
            await shots.still("02-portal-after-full", full=True)
            await page.goto(APP + "/")
            await shots.still("03-dashboard-after")
            if STAGE == "captcha":
                await mock.goto(f"{MOCK}/_switch?reset=1")

        portal = await api(page, f"/portals/{portal_id}")
        (folder / "result.json").write_text(json.dumps({"states": seen, "portal": portal}, indent=2), encoding="utf-8")
        print("states", " -> ".join(seen), flush=True)
        print("final", portal["state"], "|", portal["reason"], flush=True)
        await browser.close()


asyncio.run(main())
