"""Identify the observed listing scope without treating a complete page chain as an entire archive."""

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

from app.models import Recipe

_OPEN = {"open", "active", "current", "offers", "ongoing", "published_open"}
_WINDOW = {"latest", "recent", "newest", "last"}
_STATUS = {"state", "status", "scope", "tenderstatus", "noticestatus", "publicationstatus"}
_FILTER = {"buyer", "buyerid", "category", "categoryid", "cpv", "cpvcode", "region", "country",
           "where", "filter", "filters", "facet", "facets", "query", "q", "keyword", "keywords",
           "fromdate", "todate", "publishedfrom", "publishedto"}
_ARCHIVE = {"history", "historical", "archive", "archives"}
_SENSITIVE = re.compile("token|session|password|nonce|secret|signature|cookie|auth|csrf|verification", re.I)
_CURRENT = "Current open opportunities"
_LATEST = "Latest results window"
_HISTORICAL = "Historical archive"
_UNKNOWN = "Source listing (historical coverage unverified)"


def _key(value):
    return value.casefold().replace("_", "").replace("-", "")


def _label(key, raw):
    if raw is None or raw is False or raw == [] or raw == {}:
        return None
    value = str(raw).strip().casefold()
    if value in {"", "all", "*"}:
        return None
    if key in _WINDOW and value not in {"false", "no", "0"}:
        return f"Latest {value} results" if value.isdecimal() and value != "1" else _LATEST
    if key in _STATUS and value in _OPEN:
        return _CURRENT
    if key in {"scope", "view"}:
        if value in _WINDOW:
            return _LATEST
        if value in _ARCHIVE:
            return _HISTORICAL
    if key in _STATUS | _FILTER:
        # Show an exact public code when recognizable. Free-text and session data stay out of labels.
        code = key in {"buyerid", "categoryid", "cpv", "cpvcode"} and re.fullmatch(r"\d{1,12}", value)
        return f"Filtered results ({key}={value})" if code else f"Filtered results ({key})"
    return None


def _combined(labels: Iterable[str | None]) -> str:
    unique = list(dict.fromkeys(label for label in labels if label))
    if _LATEST in unique and any(re.fullmatch(r"Latest \d+ results", label) for label in unique):
        unique.remove(_LATEST)
    return " · ".join(sorted(unique))


def request_scope(params, *, graphql_criteria=()) -> str:
    """Describe filters on the public POST. Store no request body or free-text values."""
    def labels(mapping):
        for raw_key, value in mapping.items():
            key = _key(raw_key)
            if _SENSITIVE.search(key):
                continue
            if isinstance(value, dict) and key in {"variables", "filter", "filters", "criteria", "search", "where"}:
                nested = list(filter(None, labels(value)))
                yield from nested
                if not nested and key in {"filter", "filters", "where", "criteria"} and any(
                    not _SENSITIVE.search(k) and v not in (None, "", [], {}, False) for k, v in value.items()
                ):
                    yield "Filtered results (filter)"
            elif key != "query" or not isinstance(value, str) or not re.search(r"[{}]", value):
                yield _label(key, value)

    public_labels = list(labels(params))
    for path, tokens in graphql_criteria:
        if len(tokens) == 1 and isinstance(tokens[0], str):
            value = tokens[0].strip('"')
        else:
            value = tokens
        public_labels.append(_label(path[-1], value))
    return _combined(public_labels)


def heading_scope(text: str) -> str:
    """Only use declarations in the listing's heading or its observed navigation label."""
    words = " ".join(text.casefold().split())
    if len(words) > 180:
        return ""
    if match := re.search(r"\b(?:latest|last|most recent)\s+(\d{1,6})\b", words):
        return f"Latest {match[1]} results"
    notice = r"(?:tenders?|notices?|opportunities|calls for tenders?|procurements?)"
    if re.search(rf"\b(?:latest|recent|newest)\s+{notice}\b", words):
        return _LATEST
    if re.search(rf"\b(?:open|current|active|ongoing)\s+{notice}\b|\bmarchés en cours\b", words):
        return _CURRENT
    if re.search(rf"\b(?:{notice}\s+archive|historical\s+{notice}|archived\s+{notice})\b", words):
        return _HISTORICAL
    return ""


def _url_constraints(url):
    parts = urlsplit(url)
    pairs = [(_key(key), value) for key, value in parse_qsl(parts.query)]
    # A source can encode the exact same facet in the path.
    segments = [unquote(segment).lower() for segment in parts.path.split("/") if segment]
    for key, value in zip(segments, segments[1:]):
        if key in {"buyers", "categories", "regions", "cpv"} and not value.endswith((".html", ".aspx")):
            pairs.append(({"buyers": "buyer", "categories": "category", "regions": "region"}.get(key, key), value))
    return {(key, value) for key, value in pairs if _label(key, value)}


def url_scope(url) -> list[str]:
    parts = urlsplit(url)
    labels = [_label(key, value) for key, value in sorted(_url_constraints(url))]
    path_words = " ".join(re.findall(r"[a-z]+|\d+", parts.path.lower()))
    words = set(path_words.split())
    labels.append(heading_scope(path_words))
    if words & (_WINDOW - {"last"}):
        labels.append(_LATEST)
    if words & _ARCHIVE:
        labels.append(_HISTORICAL)
    if words & {"open", "current", "active"} and words & {"tenders", "notices", "opportunities"}:
        labels.append(_CURRENT)
    return [label for label in _combined(labels).split(" · ") if label]


def monitoring_scope(entry_url: str, recipe: Recipe) -> str:
    labels = url_scope(entry_url) + url_scope(recipe.list_.url)
    if recipe.list_.scope_label:
        labels.extend(recipe.list_.scope_label.split(" · "))
    if recipe.list_.search and recipe.list_.search.scope:
        labels.extend(recipe.list_.search.scope.split(" · "))
    return _combined(labels) or _UNKNOWN


# Filters that select other notices: a buyer, category, CPV code, region or keyword.
_NARROWING = {"buyer", "buyerid", "category", "categoryid", "cpv", "cpvcode", "region", "where", "filter",
              "filters", "facet", "facets", "query", "q", "keyword", "keywords"}


def _narrowing(url) -> set[tuple[str, str]]:
    return {(key, value) for key, value in _url_constraints(url) if key in _NARROWING}


def _window_or_archive(labels: Iterable[str]) -> bool:
    return any(label in {_LATEST, _HISTORICAL} or re.fullmatch(r"Latest \d+ results", label) for label in labels)


def scope_known(scope: str | None) -> bool:
    return bool(scope) and scope != _UNKNOWN


def archive_open_view(page, source: str) -> str | None:
    """Use an observed aggregate status control in a public GET form on this listing.

    Restrict the change to the status value. Keep the original query, buyer, region
    and all successful form defaults. A single procurement phase is not an aggregate.
    """
    if page is None:
        return None
    original = urlsplit(source)
    initial = dict(parse_qsl(original.query, keep_blank_values=True))
    for form in page.xpath("//form[.//select]"):
        action = urlsplit(urljoin(source, form.get("action") or source))
        if ((action.scheme, action.netloc, action.path) != (original.scheme, original.netloc, original.path)
                or form.get("method", "get").lower() != "get" or action.fragment
                or any(k.startswith("on") for k in form.attrib)):
            continue
        defaults = dict(parse_qsl(action.query, keep_blank_values=True))
        choice = None
        for node in form.xpath(".//input[@name] | .//select[@name] | .//textarea[@name]"):
            name = node.get("name")
            kind = node.get("type", "text").lower()
            if node.xpath("ancestor-or-self::*[@disabled]") or kind in {"submit", "button"}:
                continue
            if name in defaults or "form" in node.attrib or _SENSITIVE.search(name):
                break
            if node.tag == "select" and "multiple" not in node.attrib:
                options = node.xpath("./option[not(@disabled)] | ./optgroup[not(@disabled)]/option[not(@disabled)]")
                if not options:
                    break
                selected = next((o for o in options if "selected" in o.attrib), options[0])
                value = selected.get("value", selected.text_content())
                if _key(name) in _STATUS:
                    # In a long list of phases, aggregate options precede a disabled divider.
                    group = node.xpath("./option[@disabled][1]/preceding-sibling::option")
                    eligible = group or (options if len(options) <= 5 else [])
                    codes = {o.get("value", "").casefold(): o.get("value") for o in eligible}
                    opened = [codes[k] for k in ("active", "open", "current", "ongoing") if k in codes]
                    if (len(opened) == 1 and set(codes) & {"finished", "closed", "inactive"}
                            and initial.get(name, value).casefold() in {"", "*", "all"}):
                        choice = (name, opened[0])
            elif node.tag == "input" and kind in {"checkbox", "radio"} and "checked" not in node.attrib:
                continue
            elif node.tag == "input" and kind in {"hidden", "text", "search", "radio", "checkbox"}:
                value = node.get("value", "")
            else:
                break
            defaults[name] = value
        else:
            if choice and all(k not in defaults or defaults[k] == v for k, v in initial.items()):
                params = {**defaults, **initial, choice[0]: choice[1]}
                target = action._replace(query=urlencode(params)).geturl()
                if len(target) <= 4096 and navigation_preserves_scope(source, target):
                    return target
    return None


def is_results_window(scope: str | None) -> bool:
    """A latest-results window drops notices that are still open, so absence is no closure."""
    return any(label == _LATEST or re.fullmatch(r"Latest \d+ results", label) for label in (scope or "").split(" · "))


def navigation_preserves_scope(source, target, *, default_form=False, has_notices=False, link_label="") -> bool:
    """Whether a page the model chose keeps the requested notices.

    The portal's own redirects, feeds and blank search form may carry defaults. A model may follow
    a link to the complete or the open listing. It must not add or change a buyer, category, CPV,
    region or keyword filter, drop a constraint of the requested address, or leave a page that
    already lists notices for a latest-results window or an archive.
    """
    if default_form:
        return True
    if not _url_constraints(source).issubset(_url_constraints(target)) or _narrowing(source) != _narrowing(target):
        return False
    if has_notices:
        source_labels = set(url_scope(source))
        added = set(url_scope(target)) - source_labels
        if (label := heading_scope(link_label)) and label not in source_labels:
            added.add(label)
        if _window_or_archive(added):
            return False
    return True


def preserves_scope(entry_url: str, old: Recipe, new: Recipe) -> bool:
    """A repair may move the listing or change how it is read, but it must not select other notices."""
    if _narrowing(old.list_.url) != _narrowing(new.list_.url):
        return False
    old_selection = old.list_.search.selection_id if old.list_.search else None
    new_selection = new.list_.search.selection_id if new.list_.search else None
    return new_selection is None or new_selection == old_selection
