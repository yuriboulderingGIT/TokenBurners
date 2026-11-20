"""Read tenders and document links from a saved recipe without model calls."""

import logging
import re
from collections.abc import Callable, Iterator
from itertools import islice
from typing import Any
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit

import httpx
from jsonpath_ng import parse as parse_jsonpath
from jsonpath_ng.exceptions import JSONPathError
from lxml import etree, html

from app.fetcher import PortalFetcher
from app.models import RawTender, Recipe
from brain.blocks import Blocked, detect_block
from brain.shrink import shrink

MAX_PAGES = 100
log = logging.getLogger(__name__)
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_STALE_ENDPOINT_STATUSES = {400, 405, 422}
_NEXT_PAGE_LABELS = {
    "next page", "nächste seite", "volgende pagina", "page suivante",
    "siguiente página", "página siguiente", "pagina successiva", "następna strona",
    "página seguinte", "próxima página",
}
_NEXT_LABELS = {
    "next", "weiter", "nächste", "nächster", "volgende", "suivant", "suivante",
    "siguiente", "successiva", "successivo", "następna", "dalej", "seguinte", "próxima",
}
_PAGE_QUERY_KEYS = {
    "p", "page", "pagenumber", "pageindex", "offset", "skip", "start", "cursor", "seite", "pagina",
}
_PAGER_CONTEXT = re.compile(r"\b(?:pagination|paginator|paging|pager|page navigation)\b", re.I)
_PAGE_PATH_SUFFIX = re.compile(r"/(?:page|seite|pagina)/[0-9]+/?$", re.I)

Selector = Callable[[Any], list[Any]]
Origin = tuple[str, str, int]


class RecipeReadError(ValueError):
    """A page or selector prevents the recipe from being read reliably."""


class _PageGone(RecipeReadError):
    """The endpoint returned HTTP 404 or 410."""


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
    if not 1 <= recipe.list_.max_pages <= MAX_PAGES:
        raise RecipeReadError(f"max_pages must be between 1 and {MAX_PAGES}")

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
    observed_count = advertised_count = 0
    sampled = skipped = 0
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
        if not entries and page_number > 1:
            raise PaginationError(f"list.item found no tenders on page {page_number}", sample_page)
        observed_count += len(entries)
        advertised_count = max(advertised_count, _advertised_total_count(page))
        try:
            advertised = _advertised_next_urls(page, current_url)
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
        if following is None and observed_count < advertised_count:
            raise PaginationError(
                f"the JSON list advertised {advertised_count} tenders, but pagination exposed "
                f"only {observed_count} entries"
            )
        if not entries:
            return []

        if max_tenders is not None:
            entries = entries[:max_tenders - sampled]
        sampled += len(entries)
        for entry in entries:
            source = entry
            tender_url = current_url
            if recipe.detail.link is not None:
                link = _navigation_url(
                    current_url, detail(entry), origin, "detail.link", source=entry
                )
                if link is None:
                    raise RecipeReadError("detail.link found no URL for a list entry")
                try:
                    detail_response = await fetch_page(fetch, link, origin)
                except _PageGone:
                    skipped += 1
                    continue
                tender_url = str(detail_response.url)
                source = _parse_page(detail_response, recipe.kind)

            values = {
                name: _field_value(selector(source))
                for name, selector in fields.items()
            }
            document_urls = list(dict.fromkeys(
                url
                for value in _strings(documents(source), links=True)
                if (url := _resolve_http_url(tender_url, value)) is not None
                and _observed_href(source, tender_url, url)
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
        if following is None or (not verify_all_pages and max_tenders == sampled):
            break
        page_url = following
    else:
        raise PaginationError("pagination exceeded the recipe's max_pages")
    if skipped:
        message = f"{skipped} of {sampled} tender detail pages returned HTTP 404 or 410"
        log.info("%s: %s", recipe.list_.url, message)
        # Tolerate one removed notice, or up to 20% on larger listings.
        if not tenders or (skipped >= 2 and skipped * 5 > sampled):
            raise RecipeReadError(message)
    return tenders


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


async def fetch_page(
    fetch: PortalFetcher, url: str, origin: Origin, *, entry_redirects: bool = False,
) -> httpx.Response:
    """Check redirects before requesting them, under the same portal rate limit."""
    if portal_origin(url) != origin:
        raise RecipeReadError("page URL is outside the recipe's origin")
    for _ in range(_MAX_REDIRECTS + 1):
        response = await fetch.get(url, follow_redirects=False)
        if response.status_code not in _REDIRECT_STATUSES:
            if not response.is_success:
                _check_block(response)
                message = f"portal returned HTTP {response.status_code}"
                if response.status_code in {404, 410}:
                    raise _PageGone(message)
                if response.status_code in _STALE_ENDPOINT_STATUSES:
                    raise RecipeReadError(message)
                raise httpx.HTTPStatusError(message, request=response.request, response=response)
            return response
        redirect = _resolve_http_url(url, response.headers.get("Location", ""))
        target = portal_origin(redirect) if redirect else None
        if target is None or (target != origin and not (entry_redirects and canonical_origin(origin, target))):
            _check_block(response)
            raise RecipeReadError("portal redirected outside the recipe's origin")
        url = redirect
        origin = target
    raise RecipeReadError("portal exceeded the redirect limit")


def canonical_origin(start: Origin, target: Origin) -> bool:
    """Allow an HTTPS upgrade or a single www alias on a portal's entry URL."""
    scheme, host, port = start
    new_scheme, new_host, new_port = target
    if host.removeprefix("www.") != new_host.removeprefix("www."):
        return False
    if scheme == new_scheme:
        return port == new_port
    return scheme == "http" and new_scheme == "https" and (port == new_port or (port, new_port) == (80, 443))


def is_json_response(response: httpx.Response) -> bool:
    media_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type == "application/json" or media_type.endswith("+json"):
        return True
    if response.content.lstrip()[:1] in (b"{", b"["):
        try:
            return isinstance(response.json(), (dict, list))
        except (ValueError, RecursionError):
            pass
    return False


def _parse_page(response: httpx.Response, kind: str) -> Any:
    if kind == "html" and is_json_response(response):
        raise RecipeReadError("the portal returned JSON, but the recipe expects HTML")
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
    if not (advertised and url in advertised) and not _observed_href(source, base_url, url):
        raise RecipeReadError(f"{label} selected a URL absent from the page's links")
    return url


def _advertised_next_urls(page: Any, base_url: str) -> set[str]:
    """Independent evidence that treating this listing as the final page would lose rows."""
    links = []
    if isinstance(page, html.HtmlElement):
        for node in page.xpath(
            "descendant-or-self::a[@href] | descendant-or-self::button | "
            "descendant-or-self::link[@href and @rel]"
        ):
            if _is_next_page_link(node, base_url):
                links.append(node.get("href", ""))
    elif isinstance(page, dict):
        # A JSON 'next' can also be an opaque cursor. Only explicit URL references count.
        for container in (page, page.get("links"), page.get("_links"), page.get("pagination")):
            if isinstance(container, dict):
                value = container.get("next")
                if isinstance(value, dict):
                    value = value.get("href")
                if isinstance(value, str) and value.startswith(("/", "?", "./", "../", "http://", "https://")):
                    links.append(value)
    else:
        return set()
    urls = {url for link in links if (url := _resolve_http_url(base_url, link))}
    if isinstance(page, html.HtmlElement) and links and not urls:
        raise RecipeReadError("the next page control provides no public HTTP link")
    return urls


def _is_next_page_link(node: html.HtmlElement, base_url: str) -> bool:
    context = [node, *islice(node.iterancestors(), 4)]
    if any(_is_disabled(control) for control in context):
        return False
    if "next" in node.get("rel", "").lower().split():
        return True
    labels = {
        " ".join(value.casefold().split()).strip(" ›»→←‹«.")
        for value in (node.get("aria-label", ""), node.get("title", ""), node.text_content())
    }
    if labels & _NEXT_PAGE_LABELS:
        return True
    if not labels & _NEXT_LABELS:
        return False
    return _has_paging_url(base_url, node.get("href", "")) or any(
        _PAGER_CONTEXT.search(control.get(attribute, "").replace("_", " "))
        for control in context
        for attribute in ("class", "id", "aria-label")
    )


def _is_disabled(node: html.HtmlElement) -> bool:
    return (
        "disabled" in node.attrib or "hidden" in node.attrib
        or node.get("aria-disabled", "").lower() == "true"
        or node.get("aria-hidden", "").lower() == "true"
        or "disabled" in node.get("class", "").casefold().split()
    )


def _has_paging_url(base_url: str, href: str) -> bool:
    target = _resolve_http_url(base_url, href)
    if target is None or portal_origin(target) != portal_origin(base_url):
        return False
    base, following = urlsplit(base_url), urlsplit(target)
    if following.path != base.path:
        return bool(
            _PAGE_PATH_SUFFIX.search(following.path)
            and _PAGE_PATH_SUFFIX.sub("", following.path).rstrip("/")
            == _PAGE_PATH_SUFFIX.sub("", base.path).rstrip("/")
        )
    previous_query = parse_qs(base.query)
    return any(
        key.casefold().replace("_", "").replace("-", "") in _PAGE_QUERY_KEYS
        and previous_query.get(key) != value
        for key, value in parse_qs(following.query).items()
    )


def _advertised_total_count(page: Any) -> int:
    """Read explicit collection totals on the JSON envelope, outside the result records."""
    if not isinstance(page, dict):
        return 0
    meta = page.get("meta")
    containers = [page, page.get("pagination"), meta]
    if isinstance(meta, dict):
        containers.append(meta.get("pagination"))
    total = 0
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("total_count", "totalCount"):
            value = container.get(key)
            if isinstance(value, str) and len(value) <= 18 and re.fullmatch(r"[0-9]+", value):
                value = int(value)
            if type(value) is int:
                total = max(total, value)
    return total


def _observed_href(source: Any, base_url: str, url: str) -> bool:
    if not isinstance(source, html.HtmlElement):
        return True  # JSONPath selects values from the response itself.
    return any(
        _resolve_http_url(base_url, href) == url
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
