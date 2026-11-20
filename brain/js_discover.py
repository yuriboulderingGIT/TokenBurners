"""JS portals: find the hidden JSON API with Playwright. OWNER: B2.

Only used during attach and repair, never during regular reads.
Open the page in Chromium, record network calls, find the JSON endpoint that lists tenders,
and build a json_api recipe.

Politeness: register a route handler that blocks images, fonts and CSS and calls
`await fetch.wait_turn(url)` before letting any other request through, so the browser
also respects 1 request per second.
"""

from app.fetcher import PortalFetcher
from app.models import Recipe
from brain.llm import ScopedLLM


async def discover_json_api(url: str, fetch: PortalFetcher, llm: ScopedLLM) -> Recipe | None:
    # TODO(B2, B6)
    return None
