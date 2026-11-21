"""Recheck previously verified detail pages when notices leave the discovery window."""

import httpx

from app.models import RawTender
from brain.blocks import Blocked
from brain.normalize import _single_text, normalize
from brain.run_recipe import RecipeReadError, _check_block, _field_value, _parse_page, _selector, fetch_page, portal_origin


async def recheck_missing(db, portal_id, recipe, listed, fetch):
    """Rotate through missing open notices. An unavailable detail supplies no status evidence."""
    observations = {}
    if not recipe.detail.link or not {"status", "submissionDeadline"}.intersection(recipe.fields):
        return observations
    origin = portal_origin(recipe.list_.url)
    selectors = {k: _selector(recipe.kind, v, k) for k, v in recipe.fields.items()
                 if k in {"id", "status", "submissionDeadline"}}
    for row in db.unlisted_active_details(portal_id, listed):
        ident, url = row["id"], row["detail_url"]
        if origin is None or portal_origin(url) != origin:
            continue
        observations[ident] = None  # schedule other notices first on the next pass
        try:
            response = await fetch_page(fetch, url, origin)
            if recipe.kind == "html":
                _check_block(response)
            page = _parse_page(response, recipe.kind)
            fields = {key: _field_value(selector(page)) for key, selector in selectors.items()}
            tender = normalize(RawTender(url=str(response.url), fields=fields), recipe.locale)
            if tender and tender.id == ident:
                observations[ident] = (tender, _single_text(fields.get("status")))
        except (RecipeReadError, Blocked, httpx.HTTPError):
            pass
        if len(observations) >= 5:
            break
    return observations
