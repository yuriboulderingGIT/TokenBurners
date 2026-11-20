"""Find and sample a replacement for a failing recipe."""

import json
from collections.abc import Iterator
from itertools import islice

import httpx
from lxml import html

from app.fetcher import PortalFetcher
from app.models import Health, Recipe, Tender
from brain.blocks import Blocked
from brain.js_discover import discover_json_api
from brain.learn import (
    PageSample, RecipeValidationError, StoredTenderIds, _page_sample, _script_shell, contains_stored_id, detail_sample,
    propose_html_recipe, read_page_sample, validate_candidate,
)
from brain.llm import ScopedLLM
from brain.normalize import _single_text
from brain.run_recipe import (
    Origin, PaginationError, RecipeReadError, _field_value, _navigation_url, _observed_href, _selector,
    canonical_origin, document_base, fetch_page, is_json_response, portal_origin,
)

MAX_TRIES = 3
_MAX_DETAIL_PROBES = 3


def _entries_with_links(
    recipe: Recipe, listing: PageSample, origin: Origin,
) -> Iterator[tuple[object, str | None]]:
    if listing.document is None:
        return
    entries = _selector("html", recipe.list_.item, "list.item")(listing.document)
    link = _selector("html", recipe.detail.link, "detail.link")
    for entry in entries:
        yield entry, _navigation_url(document_base(listing.document, listing.url), link(entry), origin,
                                     "detail.link", source=entry)


async def _repair_detail(
    recipe: Recipe, listing: PageSample, fetch: PortalFetcher, origin: Origin, stored: StoredTenderIds | None,
) -> tuple[PageSample | None, str | None]:
    if not stored:
        try:
            links = (url for _, url in _entries_with_links(recipe, listing, origin) if url in listing.links)
            url = next(links, None)
        except RecipeReadError:
            url = None  # The old selectors may be the reason this portal is being repaired.
        return await detail_sample(listing, fetch, origin, url), None
    matches = {url: stored.by_url[url] for url in listing.links if url in stored.by_url}
    # The portal may move detail pages along with its layout (e.g. /tender/ → /t/).
    for url, label in listing.links.items():
        if label in stored.by_title:
            matches.setdefault(url, stored.by_title[label])
    matches.pop(listing.url, None)
    # Without a fields.url override, the stored URL is the detail actually fetched on the last read.
    if recipe.detail.link and "url" not in recipe.fields:
        for url, old_id in stored.by_url.items():
            if len(matches) >= _MAX_DETAIL_PROBES:
                break
            if url != listing.url and portal_origin(url) == origin:
                matches.setdefault(url, old_id)
    for url, old_id in islice(matches.items(), _MAX_DETAIL_PROBES):
        try:
            page = await read_page_sample(url, fetch, origin)
        except (Blocked, RecipeReadError, httpx.HTTPError):
            continue
        # A new tender can reuse a title, and an archived URL can serve unrelated content.
        same_listed_url = url in listing.links and url in stored.by_url
        if page.url != listing.url and (same_listed_url or contains_stored_id(page.document, old_id)):
            return page, old_id
    return None, None


def _check_stored_detail(
    recipe: Recipe, listing: PageSample, detail: PageSample, expected: str, origin: Origin,
) -> None:
    """Check the known page even when new rows precede it in the three-tender sample."""
    if listing.document is None or detail.document is None or detail.requested_url is None:
        return
    for entry, url in _entries_with_links(recipe, listing, origin):
        if recipe.detail.link:
            if url != detail.requested_url:
                continue
            source = detail.document
        elif (
            isinstance(entry, html.HtmlElement)
            and _observed_href(entry, document_base(listing.document, listing.url), detail.requested_url)
            and contains_stored_id(entry, expected)
        ):
            # A row can also link to related tenders. Pair the link with the actual ID on this row.
            source = entry
        else:
            continue
        read_id = _selector("html", recipe.fields.get("id"), "id")
        actual = _single_text(_field_value(read_id(source)))
        if actual != expected:
            raise RecipeValidationError(
                f"the sampled tender at {detail.requested_url} must keep its stored ID {expected!r}, "
                f"but fields.id returned {actual!r}"
            )
        return


async def repair(
    recipe: Recipe, health: Health, fetch: PortalFetcher, llm: ScopedLLM, *,
    pagination_page: dict[str, str] | None = None,
    portal_url: str | None = None,
    stored_tenders: list[Tender] | None = None,
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
    stored_ids = StoredTenderIds.from_tenders(stored_tenders, recipe) if stored_tenders else None
    html_listing = None
    json_entry = None
    if recipe.kind == "html" and recipe.list_.search is None:
        try:
            response = await fetch_page(fetch, recipe.list_.url, origin)
            if is_json_response(response):
                json_entry = response
                discovery_url = str(response.url)
            else:
                listing = _page_sample(response, origin, stored_ids, requested_url=recipe.list_.url)
                if _script_shell(listing):
                    discovery_url = listing.url
                else:
                    html_listing = listing
                    detail, detail_stored_id = await _repair_detail(recipe, listing, fetch, origin, stored_ids)
        except (RecipeReadError, httpx.HTTPError):
            return None

    feedback = list(health.problems)
    for _ in range(MAX_TRIES):
        try:
            if html_listing is not None:
                candidate = await propose_html_recipe(
                    html_listing, detail, fetch, llm, strong=True, feedback=feedback, previous=recipe,
                    pagination_page=pagination_page, expected_document_ids=health.missing_document_ids,
                    stored_ids=stored_ids, detail_stored_id=detail_stored_id,
                )
                if detail and detail_stored_id:
                    _check_stored_detail(candidate, html_listing, detail, detail_stored_id, origin)
                return candidate
            candidate = await discover_json_api(
                discovery_url, fetch, llm, previous_recipe=recipe, health=Health(ok=False, problems=feedback),
                entry_response=json_entry,
            )
            if candidate is None:
                feedback.append("the public JSON list could not be rediscovered")
                continue
            if portal_origin(candidate.list_.url) != origin:
                raise RecipeValidationError("discovered JSON URL belongs to another portal")
            await validate_candidate(
                candidate, fetch, expected_document_ids=health.missing_document_ids, stored_ids=stored_ids
            )
            return candidate
        except (RecipeReadError, RecipeValidationError, httpx.HTTPError, json.JSONDecodeError) as exc:
            feedback.append(str(exc))
            if isinstance(exc, PaginationError) and exc.page_sample:
                pagination_page = exc.page_sample
    return None
