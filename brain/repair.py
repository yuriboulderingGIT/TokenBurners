"""Repair loop. OWNER: B1.

Old recipe + new shrunk HTML + failed checks -> LLM rewrites the recipe -> test it ->
accept, or retry (max 3 tries) -> None (the portal goes to failed).
Use the strong model here (llm.json(..., strong=True)).

STUB: always gives up.
"""

from app.fetcher import PortalFetcher
from app.models import Health, Recipe
from brain.llm import ScopedLLM

MAX_TRIES = 3


async def repair(recipe: Recipe, health: Health, fetch: PortalFetcher, llm: ScopedLLM) -> Recipe | None:
    # TODO(B1, B7)
    return None
