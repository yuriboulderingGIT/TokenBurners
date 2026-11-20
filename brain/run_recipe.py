"""Read tenders and document links from a saved recipe without model calls."""

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from jsonpath_ng import parse as parse_jsonpath
from jsonpath_ng.exceptions import JSONPathError
from lxml import etree, html

from app.fetcher import PortalFetcher
from app.models import RawTender, Recipe
from brain.blocks import Blocked, detect_block
from brain.shrink import shrink

_MAX_PAGES = 100
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Selector = Callable[[Any], list[Any]]
Origin = tuple[str, str, int]


class RecipeReadError(ValueError):
    """A page or selector prevents the recipe from being read reliably."""


class PaginationError(RecipeReadError):
    """The observed page chain is incomplete. Discard its rows and repair the recipe."""

    def __init__(self, message: str, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.page_sample = (
            {"url": str(response.url), "html": shrink(response.text, max_chars=16_000)}
            if response is not None else None
        )


async def run_recipe(
    recipe: Recipe, fetch: PortalFetcher, *, max_tenders: int | None = None,
    verify_all_pages: bool = False,
) -> list[RawTender]:
    """Sample at most max_tenders details; validation can still verify every list page."""
    if max_tenders is not None and max_tenders < 0:
        raise ValueError("max_tenders must be nonnegative")
    if max_tenders == 0:
        return []
    if not 1 <= recipe.list_.max_pages <= _MAX_PAGES:
        raise RecipeReadError(f"max_pages must be between 1 and {_MAX_PAGES}")

    page_url = recipe.list_.url.strip()
    origin = portal_origin(page_url)
    if origin is None:
        raise RecipeReadError("recipe list URL must be an HTTP(S) URL without credentials")
    page_url = str(httpx.URL(urldefrag(page_url).url))
    item = _selector(recipe.kind, recipe.list_.item, "list.item")
    try:
        next_page = _selector(recipe.kind, recipe.list_.next_page, "list.next_page")
    except RecipeReadError as exc:
        raise PaginationError(str(exc)) from exc
    detail = _selector(recipe.kind, recipe.detail.link, "detail.link")
    fields = {
        name: _selector(recipe.kind, expression, name)
        for name, expression in recipe.fields.items()
    }
    documents = _selector(recipe.kind, recipe.documents.link, "documents.link")

    tenders: list[RawTender] = []
    seen_pages: set[str] = set()
    for page_number in range(1, recipe.list_.max_pages + 1):
        if page_url in seen_pages:
            raise PaginationError("pagination revisited a list page")
        response = await fetch_page(fetch, page_url, origin)
        current_url = str(response.url)
        if current_url in seen_pages:
            raise PaginationError("pagination redirected to a previous list page")
        seen_pages.update((page_url, current_url))
        page = _parse_page(response, recipe.kind)
        entries = [entry for entry in _flatten(item(page)) if entry is not None]
        if recipe.kind == "html":
            _check_block(response, content_available=bool(entries))

        sample_page = response if recipe.kind == "html" else None
        if not entries:
            if page_number > 1:
                raise PaginationError(f"list.item found no tenders on page {page_number}", sample_page)
            return []
        advertised = _advertised_next_urls(page, current_url)
        try:
            following = _navigation_url(
                current_url, next_page(page), origin, "list.next_page", source=page,
                advertised=advertised,
            )
        except RecipeReadError as exc:
            raise PaginationError(str(exc), sample_page) from exc
        if advertised and following not in advertised:
            raise PaginationError(
                f"list.next_page missed the advertised next page on page {page_number}", sample_page
            )

        if max_tenders is not None:
            entries = entries[:max_tenders - len(tenders)]
        for entry in entries:
            source = entry
            tender_url = current_url
            if recipe.detail.link is not None:
                link = _navigation_url(
                    current_url, detail(entry), origin, "detail.link", source=entry
                )
                if link is None:
                    raise RecipeReadError("detail.link found no URL for a list entry")
                detail_response = await fetch_page(fetch, link, origin)
                tender_url = str(detail_response.url)
                source = _parse_page(detail_response, recipe.kind)

            values = {
                name: _field_value(selector(source))
                for name, selector in fields.items()
            }
            document_urls = list(dict.fromkeys(
                url
                for value in _strings(documents(source), links=True)
                if (url := resolve_portal_url(tender_url, value, origin)) is not None
                and _observed_href(source, tender_url, url, origin)
            ))
            if recipe.kind == "html" and recipe.detail.link is not None:
                _check_block(
                    detail_response, content_available=bool(values.get("id") or document_urls)
                )
            if isinstance(values.get("url"), str):
                # Public feeds may link to a separate notice website. This value is
                # delivered as metadata only, after all fetch destinations are chosen.
                if recipe.kind == "json_api":
                    tender_url = _resolve_http_url(tender_url, values["url"]) or tender_url
                else:
                    tender_url = resolve_portal_url(tender_url, values["url"], origin) or tender_url
            tenders.append(RawTender(url=tender_url, fields=values, document_links=document_urls))
        if following is None or (not verify_all_pages and max_tenders == len(tenders)):
            return tenders
        page_url = following
    raise PaginationError("pagination exceeded the recipe's max_pages")


def _selector(kind: str, expression: str | None, label: str) -> Selector:
    if expression is None:
        return lambda _: []
    if kind == "json_api":
        try:
            path = parse_jsonpath(expression)
        except JSONPathError as exc:
            raise RecipeReadError(f"invalid JSONPath for {label}") from exc
        return lambda value: [match.value for match in path.find(value)]
    try:
        path = etree.XPath(expression, regexp=False)
    except etree.XPathError as exc:
        raise RecipeReadError(f"invalid XPath for {label}") from exc

    def select(value: Any) -> list[Any]:
        try:
            matches = path(value)
        except (etree.XPathError, TypeError) as exc:
            raise RecipeReadError(f"could not evaluate XPath for {label}") from exc
        return matches if isinstance(matches, list) else [matches]

    return select


async def fetch_page(fetch: PortalFetcher, url: str, origin: Origin) -> httpx.Response:
    """Check redirects before requesting them, under the same portal rate limit."""
    if portal_origin(url) != origin:
        raise RecipeReadError("page URL is outside the recipe's origin")
    for _ in range(_MAX_REDIRECTS + 1):
        response = await fetch.get(url, follow_redirects=False)
        if response.status_code not in _REDIRECT_STATUSES:
            if not response.is_success:
                _check_block(response)
                raise RecipeReadError(f"portal returned HTTP {response.status_code}")
            return response
        redirect = resolve_portal_url(url, response.headers.get("Location", ""), origin)
        if redirect is None:
            _check_block(response)
            raise RecipeReadError("portal redirected outside the recipe's origin")
        url = redirect
    raise RecipeReadError("portal exceeded the redirect limit")


def _parse_page(response: httpx.Response, kind: str) -> Any:
    try:
        if kind == "json_api":
            return response.json()
        return html.fromstring(response.text)
    except (ValueError, etree.ParserError) as exc:
        _check_block(response)
        raise RecipeReadError(f"could not parse portal response as {kind}") from exc


def _check_block(response: httpx.Response, *, content_available: bool = False) -> None:
    kind = detect_block(response.text, response.status_code, content_available=content_available)
    if kind:
        raise Blocked(kind)


def _navigation_url(
    base_url: str, matches: list[Any], origin: Origin, label: str, *, source: Any,
    advertised: set[str] | None = None,
) -> str | None:
    values = [value for value in _flatten(matches) if value is not None and value != ""]
    if not values:
        return None
    links = list(_strings(values, links=True))
    if len(links) != len(values) or len(set(links)) != 1:
        raise RecipeReadError(f"{label} must select a single URL")
    url = resolve_portal_url(base_url, links[0], origin)
    if url is None:
        raise RecipeReadError(f"{label} selected an invalid or off-origin URL")
    if not (advertised and url in advertised) and not _observed_href(source, base_url, url, origin):
        raise RecipeReadError(f"{label} selected a URL absent from the page's links")
    return url


def _advertised_next_urls(page: Any, base_url: str) -> set[str]:
    """Independent evidence that treating this listing as the final page would lose rows."""
    links = []
    if isinstance(page, html.HtmlElement):
        for node in page.xpath("descendant-or-self::a[@href] | descendant-or-self::link[@href and @rel]"):
            if "disabled" in node.attrib or node.get("aria-disabled", "").lower() == "true":
                continue
            rel = node.get("rel", "").lower().split()
            label = " ".join((node.get("aria-label") or node.text_content()).casefold().split())
            if "next" in rel or label == "next page":
                links.append(node.get("href"))
    elif isinstance(page, dict):
        # A JSON 'next' can also be an opaque cursor. Only explicit URL references count.
        for container in (page, page.get("links"), page.get("_links")):
            if isinstance(container, dict):
                value = container.get("next")
                if isinstance(value, dict):
                    value = value.get("href")
                if isinstance(value, str) and value.startswith(("/", "?", "./", "../", "http://", "https://")):
                    links.append(value)
    else:
        return set()
    return {url for link in links if (url := _resolve_http_url(base_url, link))}


def _observed_href(source: Any, base_url: str, url: str, origin: Origin) -> bool:
    if not isinstance(source, html.HtmlElement):
        return True  # JSONPath selects values from the response itself.
    return any(
        resolve_portal_url(base_url, href, origin) == url
        for href in source.xpath("descendant-or-self::a/@href")
    )


def resolve_portal_url(base_url: str, value: str, origin: Origin) -> str | None:
    url = _resolve_http_url(base_url, value)
    return url if url and portal_origin(url) == origin else None


def _resolve_http_url(base_url: str, value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith("#") or _invalid_url_characters(value):
        return None
    try:
        url = urldefrag(urljoin(base_url, value)).url
        return str(httpx.URL(url)) if portal_origin(url) is not None else None
    except ValueError:
        return None


def portal_origin(url: str) -> Origin | None:
    if _invalid_url_characters(url):
        return None
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        parsed = httpx.URL(url)
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme, parsed.host, port
    except (ValueError, httpx.InvalidURL):
        return None


def _invalid_url_characters(url: str) -> bool:
    return "\\" in url or any(ord(char) < 32 for char in url)


def _flatten(matches: list[Any]) -> Iterator[Any]:
    for value in matches:
        if isinstance(value, list):
            yield from value
        else:
            yield value


def _strings(matches: list[Any], *, links: bool = False) -> Iterator[str]:
    for value in _flatten(matches):
        if isinstance(value, html.HtmlElement):
            value = value.get("href") if links else value.text_content()
        if not links and type(value) in (int, float):
            value = str(value)
        if isinstance(value, str) and (value := value.strip()):
            yield value


def _field_value(matches: list[Any]) -> str | list[str] | None:
    values = list(dict.fromkeys(_strings(matches)))
    if len(values) == 1:
        return values[0]
    return values or None
