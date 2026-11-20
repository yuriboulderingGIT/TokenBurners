"""Anonymous search requests observed during a public browser visit."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import httpx
from lxml import html

from app.fetcher import PortalFetcher
from app.models import SearchTransport
from brain.run_recipe import MAX_PAGES, PaginationError, RecipeReadError, _advertised_total_count, portal_origin


# Session-bound tokens a page's code sends with its requests; observed afresh on every visit.
ANTI_FORGERY_HEADERS = re.compile(r"(?:x-)?(?:csrf|xsrf)-?token|(?:x-)?requestverificationtoken", re.I)


class PublicVisit:
    """Isolate a listing's session cookies while sharing the normal fetcher's rate limiter."""

    def __init__(self, fetch: PortalFetcher):
        self.fetch = fetch
        self.cookies = httpx.Cookies()

    async def get(self, url, *, headers=None, **kwargs):
        request = httpx.Request("GET", url)
        self.cookies.set_cookie_header(request)
        headers = {**(headers or {}), "Cookie": request.headers.get("cookie", "")}
        response = await self.fetch.get(url, headers=headers, **kwargs)
        self.cookies.extract_cookies(response)
        return response


def api_subdomain(page, target) -> bool:
    """An HTTPS child host of the portal's own host, such as api.portal.example for portal.example."""
    return bool(page and target and page[0] == target[0] == "https" and page[2] == target[2] == 443
                and target[1].endswith("." + page[1]))


def eligible_endpoint(page_url: str, url: str) -> bool:
    page, target = portal_origin(page_url), portal_origin(url)
    if not page or not target:
        return False
    if any(re.search("token|session|password|secret|key|signature", key, re.I)
           for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
        return False
    if target != page and not api_subdomain(page, target):
        return False
    # Only anonymous XHR/fetch requests to identifiable read operations qualify.
    path = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", path).lower()
    return bool(re.fullmatch(r"(?:search|query|find|list|results|next publications|graphql|"
                             r"get (?:results|notices|publications|tenders)(?: by [a-z ]+)?)", words))


@dataclass
class ObservedSearch:
    spec: SearchTransport
    params: dict = field(repr=False)
    visit: PublicVisit = field(repr=False)
    source_total: int = 0
    headers: dict = field(default_factory=dict, repr=False)

    @classmethod
    def observe(cls, source, request, visit, total):
        if not eligible_endpoint(source, request.url) or request.headers.get("authorization"):
            return None
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        raw = request.post_data or ""
        if len(raw) > 256_000:
            return None
        try:
            if media == "application/json":
                params = json.loads(raw)
            elif media == "application/x-www-form-urlencoded":
                pairs = parse_qsl(raw, keep_blank_values=True)
                params = dict(pairs)
                if len(params) != len(pairs):
                    return None
            else:
                return None
        except ValueError:
            return None
        if not isinstance(params, dict):
            return None
        if re.search(r'"(?:password|access_token|authorization|mutation)"\s*:|\bmutation\s*[{(]', raw, re.I):
            return None
        fields = _paging_fields(params)
        if len(fields) > 1:
            return None
        path, key = fields[0] if fields else (None, None)
        spec = SearchTransport(endpoint=request.url, media_type=media, page_field=key, page_path=path or None)
        headers = {k: v for k, v in request.headers.items() if ANTI_FORGERY_HEADERS.fullmatch(k)}
        return cls(spec, params, visit, total, headers)

    async def read(self, index: int, offset: int):
        params = json.loads(json.dumps(self.params))
        key = self.spec.page_field
        if key is not None:
            try:
                container, original = at_path(params, self.spec.page_path or []), at_path(self.params, self.spec.page_path or [])
                first = int(original[key])
            except (RecipeReadError, KeyError, TypeError, ValueError) as exc:
                raise RecipeReadError("could not establish the first search page") from exc
            if key.lower() in {"offset", "start", "skip"}:
                container[key] = offset
            else:
                if first not in (0, 1):
                    raise RecipeReadError("could not establish the first search page")
                container[key] = first + index
        body = json.dumps(params).encode() if self.spec.media_type == "application/json" else urlencode(params).encode()
        url = self.spec.endpoint
        for _ in range(4):
            response = await self.visit.fetch.public_search(url, content=body, media_type=self.spec.media_type,
                                                          cookies=self.visit.cookies, headers=self.headers)
            self.visit.cookies.extract_cookies(response)
            if response.status_code not in (307, 308):
                break
            target = urljoin(url, response.headers.get("location", ""))
            if portal_origin(target) != portal_origin(url):
                raise RecipeReadError("public search redirected outside its origin")
            url = target
        if not response.is_success:
            raise RecipeReadError(f"public search returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise RecipeReadError(f"public search returned HTTP {response.status_code} without JSON results") from exc


def _paging_fields(params, path=()):
    """Numeric page or offset parameters, also inside nested query variables such as GraphQL's."""
    found = []
    for key, value in params.items():
        if isinstance(value, dict) and len(path) < 3:
            found += _paging_fields(value, (*path, key))
        elif (key.lower() in {"offset", "start", "skip", "page", "pageindex", "pagenumber"}
              and not isinstance(value, bool) and str(value).isascii() and str(value).isdigit()):
            found.append((list(path), key))
    return found


def at_path(payload, path):
    for key in path:
        if not isinstance(payload, dict) or key not in payload:
            raise RecipeReadError("the public search response changed shape")
        payload = payload[key]
    return payload


def fragment_path(payload, path=()):
    if isinstance(payload, str) and payload.lstrip().startswith("<"):
        return list(path)
    if isinstance(payload, dict) and len(path) < 3:
        for key, item in payload.items():
            found = fragment_path(item, (*path, key))
            if found is not None:
                return found
    return None

@dataclass
class SearchPages:
    search: ObservedSearch
    spec: SearchTransport
    first_payload: object
    total: int
    seen: set[bytes] = field(default_factory=set)

    @classmethod
    async def open(cls, url, spec, fetch):
        from brain.js_discover import _capture_json

        for key, payload in await _capture_json(url, fetch, target=spec.endpoint):
            if (isinstance(key, ObservedSearch) and key.spec.endpoint == spec.endpoint
                    and key.spec.media_type == spec.media_type and key.spec.page_field == spec.page_field
                    and key.spec.page_path == spec.page_path):
                return cls(key, spec, payload, key.source_total)
        raise RecipeReadError("could not reproduce the saved anonymous search from the public listing")

    async def read(self, number, offset):
        payload = self.first_payload if number == 1 else await self.search.read(number - 1, offset)
        if self.spec.total_path:
            count = str(at_path(payload, self.spec.total_path))
            if not count.isascii() or not count.isdigit() or len(count) > 18:
                raise PaginationError("public search returned an invalid result count")
            self.total = max(self.total, int(count))
        self.total = max(self.total, _advertised_total_count(payload))
        if self.spec.html_path is None:
            return payload
        fragment = at_path(payload, self.spec.html_path)
        if not isinstance(fragment, str):
            raise RecipeReadError("public search no longer returns HTML fragments")
        return html.fragment_fromstring(fragment or "<div/>", create_parent="div")

    def more(self, entries, count):
        if not self.seen and entries and self.total > len(entries) * MAX_PAGES:
            raise PaginationError(f"public search reports {self.total} entries at {len(entries)} per page, "
                                  f"exceeding the {MAX_PAGES}-page limit")
        if entries:
            batch = [html.tostring(e, encoding="unicode") if isinstance(e, html.HtmlElement) else e for e in entries]
            digest = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).digest()
            if digest in self.seen:
                raise PaginationError("public search returned a previously seen page")
            self.seen.add(digest)
        if self.spec.page_field is None and not self.total:
            raise PaginationError("public search supplies no verifiable total or paging parameter")
        return self.spec.page_field is not None and (not self.total or count < self.total)
