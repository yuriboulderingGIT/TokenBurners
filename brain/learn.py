"""Attach: learn a recipe for a portal we have never seen. OWNER: B1.

STUB: returns a placeholder recipe without looking at the portal. Replace with:
find the list page from any start URL, pagination, detail link, label-based field XPaths,
document links; test the recipe on 3 tenders; ask the LLM to fix it if checks fail.
"""

from app.db import now_iso
from app.fetcher import PortalFetcher
from app.models import ListSpec, Recipe
from brain.llm import ScopedLLM


class AttachFailed(Exception):
    """Could not learn a working recipe. The message becomes the portal's reason."""


async def learn_recipe(url: str, fetch: PortalFetcher, llm: ScopedLLM) -> Recipe:
    # TODO(B1, B4): real attach. For JS portals, try brain.js_discover.discover_json_api() (B2).
    return Recipe(
        list=ListSpec(url=url, item="//stub"),
        learned_by="stub",
        learned_at=now_iso(),
    )
