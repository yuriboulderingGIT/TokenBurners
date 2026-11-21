"""Anonymous search requests observed during a public browser visit."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import httpx
from lxml import etree, html

from app.fetcher import PortalFetcher
from app.models import SearchTransport
from brain.run_recipe import MAX_PAGES, PaginationError, RecipeReadError, _advertised_total_count, _flatten, _selector, _strings, portal_origin
from brain.scope import request_scope


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
    if any(_SESSION_KEY.search(_key(key)) or "key" in key.lower()
           for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
        return False
    if target != page and not api_subdomain(page, target):
        return False
    # Only anonymous XHR/fetch requests to identifiable read operations qualify.
    path = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", path).lower()
    return bool(re.fullmatch(r"(?:search|query|find|list|results|next publications|graphql|"
                             r"get (?:results|notices|publications|tenders)(?: by [a-z ]+)?)", words))


_SESSION_KEY = re.compile(
    r"token|nonce|session|password|secret|signature|cookie|verification|csrf|xsrf|authorization|credential|api.?key"
    r"|^(?:sid|jwt|bearer|timestamp|requestid|correlationid|traceid|cachebuster|_)$", re.I,
)
_GRAPHQL_TOKEN = re.compile(
    r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\#[^\n\r]*|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[_A-Za-z]\w*|[^\s,\ufeff]'
)
_PAGING_KEYS = {"page", "offset", "start", "skip", "pageindex", "pagenumber"}
# Values that select which notices a search returns. Sort order, page size and status defaults
# can change with a redesign without selecting other notices, so a repair may change them.
_SELECTING_KEYS = {
    "q", "query", "search", "searchterm", "keyword", "keywords", "term", "buyer", "buyerid", "cpv", "cpvcode",
    "region", "category", "categoryid", "facets",
}
# Inline GraphQL input objects are compared whole.
_GRAPHQL_SELECTING_KEYS = _SELECTING_KEYS | {"filter", "filters", "where", "criteria"}


def _graphql_tokens(query):
    return [token for token in _GRAPHQL_TOKEN.findall(query) if not token.startswith("#")]


def _session_variables(tokens):
    return {tokens[i + 3] for i, token in enumerate(tokens[:-3])
            if token.isidentifier() and _SESSION_KEY.search(_key(token)) and tokens[i + 1:i + 3] == [":", "$"]}


def _is_read_operation(params, url):
    query = params.get("query")
    extensions = params.get("extensions")
    graphql = (urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower() == "graphql"
               or "operationName" in params or isinstance(extensions, dict) and "persistedQuery" in extensions
               or isinstance(query, str) and "{" in query)
    if not graphql:
        return True
    if not isinstance(query, str):
        return False  # a persisted-query hash alone does not establish that it is read-only
    tokens = _graphql_tokens(query)
    if not tokens or tokens[0] not in {"query", "fragment", "{"}:
        return False
    # Operation definitions occur outside all selection sets. Quoted strings and
    # comments are already single tokens, including any operation-like text in them.
    depth = 0
    session_variables = _session_variables(tokens)
    declaration = ""
    for index, token in enumerate(tokens):
        if depth == 0 and token in {"mutation", "subscription"}:
            return False
        if (token.isidentifier() and _SESSION_KEY.search(_key(token)) and tokens[index + 1:index + 2] == [":"]
                and tokens[index - 1:index] != ["$"] and tokens[index + 2:index + 3] != ["$"]):
            return False  # the operation itself contains an inline session argument
        if token == "$" and tokens[index + 2:index + 3] == [":"]:
            declaration = tokens[index + 1]
        if (token == "=" and depth == 1
                and (_SESSION_KEY.search(_key(declaration)) or declaration in session_variables)):
            return False  # an inline session variable default also changes between visits
        if token in {"{", "(", "["}:
            depth += 1
        elif token in {"}", ")", "]"}:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and "{" in tokens


def _key(name):
    return name.lower().replace("-", "").replace("_", "")


def _public_shape(value, key=""):
    """Canonical public parameters. Ignore session, tracing, and current page position."""
    if isinstance(value, dict):
        return {k: _public_shape(v, _key(k)) for k, v in value.items()
                if not _SESSION_KEY.search(_key(k)) and k != "_"}
    if isinstance(value, list):
        return [_public_shape(item) for item in value]
    if key in _PAGING_KEYS:
        return "page"
    if key == "query" and isinstance(value, str) and "{" in value:
        return _graphql_tokens(value)
    return value


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def _public_params(params):
    public = _public_shape(params)
    query = public.get("query")
    variables = public.get("variables")
    if isinstance(params.get("query"), str) and isinstance(query, list) and isinstance(variables, dict):
        # A portal can name its nonce variable generically, e.g. csrfToken: $value.
        for key in _session_variables(query):
            variables.pop(key, None)
    return public


def operation_id(params):
    """Only the digest of a normalized public operation is stored in a recipe."""
    return _digest(_public_params(params))


def _argument_tokens(tokens, start):
    """Read one literal, variable reference, list or input object from the query."""
    if start >= len(tokens):
        return []
    if tokens[start] == "$":
        return tokens[start:start + 2]
    if tokens[start] not in {"{", "["}:
        return tokens[start:start + 1]
    depth = 0
    for i in range(start, len(tokens)):
        depth += int(tokens[i] in {"{", "["}) - int(tokens[i] in {"}", "]"})
        if depth == 0:
            return tokens[start:i + 1]
    return []


def _graphql_criteria(tokens, variables):
    """Scope includes arguments written directly in GraphQL and values supplied via variables."""
    bound = dict(variables)
    for i, token in enumerate(tokens[:-3]):
        if token == "$" and tokens[i + 2] == ":" and tokens[i + 1] not in bound:
            end = i + 3
            while end < len(tokens) and (tokens[end].isidentifier() or tokens[end] in {"[", "]", "!"}):
                end += 1
            if tokens[end:end + 1] == ["="]:
                bound[tokens[i + 1]] = _argument_tokens(tokens, end + 1)
    owner = None
    for i, token in enumerate(tokens):
        if token == "(":
            owner = tokens[i - 1] if i else None
        elif token == ")":
            owner = None
        elif owner and _key(token) in _GRAPHQL_SELECTING_KEYS and tokens[i - 1] != "$" and tokens[i + 1:i + 2] == [":"]:
            value = _argument_tokens(tokens, i + 2)
            if value:
                resolved = []
                it = iter(value)
                for part in it:
                    resolved.append(bound.get(next(it, ""), None) if part == "$" else part)
                yield (["graphql", owner, _key(token)], resolved)


def selection_id(params):
    """Digest of the public filters that select notices (buyer, category, CPV, region, keyword).

    A repair must keep them; it may change sort order, paging and the query's shape."""
    def criteria(value, path=()):
        for key, item in value.items():
            name = _key(key)
            if isinstance(item, dict):
                yield from criteria(item, (*path, key))
            elif name in _SELECTING_KEYS and not (name == "query" and isinstance(item, list)):
                if item not in (None, "", []):
                    yield ([*path, key], item)

    public = _public_params(params)
    selected = list(criteria(public))
    if isinstance(params.get("query"), str) and isinstance(public.get("query"), list):
        variables = public.get("variables")
        selected.extend(_graphql_criteria(public["query"], variables if isinstance(variables, dict) else {}))
    selected.sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    return _digest(selected) if selected else None


def matches_projection(spec, payload):
    if not spec.result_item:
        return True  # older saved recipes
    kind = "html" if spec.html_path is not None else "json_api"
    if kind == "html":
        try:
            fragment = at_path(payload, spec.html_path)
        except RecipeReadError:
            return False
        if not isinstance(fragment, str) or not fragment.strip():
            return False
        try:
            payload = html.fragment_fromstring(fragment, create_parent="div")
        except (etree.ParserError, ValueError):
            return False
    rows = list(_flatten(_selector(kind, spec.result_item, "saved search collection")(payload)))
    if not rows:
        return False
    fields = [_selector(kind, selector, name) for name, selector in spec.record_fields.items()]
    return any(all(next(_strings(select(row)), None) is not None for select in fields) for row in rows[:3])


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
        try:
            raw = request.post_data or ""
        except UnicodeDecodeError:
            return None  # a compressed or binary body has no fields to replay
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
        except (ValueError, RecursionError):
            return None
        if not isinstance(params, dict) or not _is_read_operation(params, request.url):
            return None
        if any(_key(key) in {"password", "accesstoken", "authorization", "mutation"} for key in params):
            return None
        if re.search(r'"(?:password|access_token|authorization|mutation)"\s*:', raw, re.I):
            return None
        fields = _paging_fields(params)
        if len(fields) > 1:
            return None
        path, key = fields[0] if fields else (None, None)
        try:
            spec = SearchTransport(endpoint=request.url, media_type=media, page_field=key, page_path=path or None,
                                   operation_id=operation_id(params), selection_id=selection_id(params),
                                   scope=request_scope(params) or None)
        except RecursionError:
            return None
        headers = {k: v for k, v in request.headers.items() if ANTI_FORGERY_HEADERS.fullmatch(k)}
        return cls(spec, params, visit, total, headers)

    def matches(self, spec, payload):
        return (self.spec.endpoint == spec.endpoint and self.spec.media_type == spec.media_type
                and self.spec.page_field == spec.page_field and self.spec.page_path == spec.page_path
                and (spec.operation_id is None or operation_id(self.params) == spec.operation_id)
                and matches_projection(spec, payload))

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

        matches = {}
        for key, payload in await _capture_json(url, fetch, target=spec):
            if isinstance(key, ObservedSearch) and key.matches(spec, payload):
                # The browser can issue the same operation twice with fresh session tokens.
                matches.setdefault(operation_id(key.params), (key, payload))
        if len(matches) > 1:
            raise RecipeReadError("multiple anonymous searches match the saved operation")
        if matches:
            key, payload = next(iter(matches.values()))
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
