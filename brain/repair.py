"""Replace a failing recipe only after the new recipe passes a sample read."""

import json

import httpx

from app.fetcher import PortalFetcher
from app.models import Health, Recipe
from brain.js_discover import discover_json_api
from brain.learn import (
    RecipeValidationError, detail_sample, propose_html_recipe, read_page_sample, validate_candidate,
)
from brain.llm import ScopedLLM
from brain.run_recipe import PaginationError, RecipeReadError, canonical_origin, fetch_page, portal_origin

MAX_TRIES = 3


async def repair(
    recipe: Recipe, health: Health, fetch: PortalFetcher, llm: ScopedLLM, *,
    pagination_page: dict[str, str] | None = None,
    portal_url: str | None = None,
) -> Recipe | None:
    origin = portal_origin(recipe.list_.url)
    if origin is None:
        return None
    discovery_url = portal_url or recipe.list_.url
    if recipe.kind == "json_api" and (entry_origin := portal_origin(discovery_url)) != origin:
        if entry_origin is None or not canonical_origin(entry_origin, origin):
            return None
        try:
            response = await fetch_page(fetch, discovery_url, entry_origin, entry_redirects=True)
        except (RecipeReadError, httpx.HTTPError):
            return None
        discovery_url = str(response.url)
        if portal_origin(discovery_url) != origin:
            return None
    if recipe.kind == "html":
        try:
            listing = await read_page_sample(recipe.list_.url, fetch, origin)
            detail = await detail_sample(listing, fetch, origin, next(iter(listing.links), None))
        except (RecipeReadError, httpx.HTTPError):
            return None

    feedback = list(health.problems)
    for _ in range(MAX_TRIES):
        try:
            if recipe.kind == "html":
                return await propose_html_recipe(
                    listing, detail, fetch, llm, strong=True, feedback=feedback, previous=recipe,
                    pagination_page=pagination_page, expected_document_ids=health.missing_document_ids,
                )
            candidate = await discover_json_api(
                discovery_url, fetch, llm, previous_recipe=recipe, health=Health(ok=False, problems=feedback)
            )
            if candidate is None:
                feedback.append("the public JSON list could not be rediscovered")
                continue
            if portal_origin(candidate.list_.url) != origin:
                raise RecipeValidationError("discovered JSON URL belongs to another portal")
            await validate_candidate(candidate, fetch, expected_document_ids=health.missing_document_ids)
            return candidate
        except (RecipeReadError, RecipeValidationError, httpx.HTTPError, json.JSONDecodeError) as exc:
            feedback.append(str(exc))
            if isinstance(exc, PaginationError) and exc.page_sample:
                pagination_page = exc.page_sample
    return None
