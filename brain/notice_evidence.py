"""Identify what a selected row represents using evidence from the source."""

import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from lxml import html


_NOTICE = re.compile(
    r"\b(?:tenders?|notices?|procurements?|contracts?|opportunit(?:y|ies)|cft|bekanntmachungen?|"
    r"ausschreibungen?|publications?|avis|marches?|opportunites?|zamowieni[ae]|gara|gare|"
    r"aanbestedingen?|opdrachten?|licitaciones?|appalti|bandi|przetarg(?:i|u|ach)?|zakazk[ay]|verejne zakazky|"
    r"διαγωνισμος|προκηρυξη|contratacion)\b", re.I,
)
_OTHER = re.compile(
    r"\b(?:categor(?:y|ies)|kategorien?|facets?|taxonom(?:y|ies)|cpv|buyers?|organisations?|"
    r"organizations?|authorities|suppliers?|news(?:room)?|press releases?|navigation|nav|menu|settings|configuration|"
    r"categorie[eë]n?|cat[eé]gories?|categor[ií]as?|categor[ií]e|kategorie|kategor[ií]e|"
    r"κατηγοριες|kategorie|kategorije)\b", re.I,
)
_FORM = re.compile(
    r"\b(?:search form|search criteria|formulaire de recherche|formulario de busqueda|"
    r"modulo di ricerca|zoekformulier|formularz wyszukiwania|vyhledavaci formular|"
    r"αναζητηση)\b", re.I,
)
_SOURCE_FIELDS = ("referenceNumber", "buyerName", "submissionDeadline", "procedureType", "contractNature")
_NOTICE_KEYS = {"notice id", "notice number", "tender id", "tender number", "submission deadline", "contract nature"}
_DIRECTORY_FILTER = {"cpv", "cpvcode", "category", "categoryid", "buyer", "buyerid"}
_INDEX_ROUTES = {"tenders", "notices", "contracts", "opportunities", "procurements", "publications",
                 "help", "about", "contact", "login", "register", "search", "browse"}


def _words(value: str) -> str:
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                     if not unicodedata.combining(c))
    return re.sub(r"[^\w]+|_", " ", value).strip().casefold()


def _kind(label: str) -> str | None:
    words = _words(label)
    if _OTHER.search(words):
        return "directory, news or navigation"
    if _FORM.search(words):
        return "search form"
    if _NOTICE.search(words):
        return "notice"
    return None


def _route_kind(url: str, *, record: bool = False) -> str | None:
    # A buyer's notices can live under /buyers/123/notices. The nearest typed
    # route determines the resource. Query filters do not change its type.
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    leaf = _words(unquote(parts.path).rstrip("/").rsplit("/", 1)[-1])
    if record:
        keys = {key.casefold() for key in parse_qs(parts.query)}
        if keys and keys <= _DIRECTORY_FILTER:
            return "category or buyer filter"
        if leaf in _INDEX_ROUTES and not keys:
            return "navigation to a collection or service"
    for segment in reversed(unquote(parts.path).split("/")):
        if kind := _kind(segment):
            return kind
    return None


def non_notice_target(url: str) -> str | None:
    kind = _route_kind(url, record=True)
    return None if kind == "notice" else kind


def json_collection_kinds(page: Any, entries: list) -> dict[int, str | None]:
    """Find the actual enclosing JSON keys, including when a selector uses a wildcard."""
    wanted = {id(entry) for entry in entries if isinstance(entry, dict)}
    result = {}
    pending = [(page, None)]
    while pending and wanted:
        value, kind = pending.pop()
        if id(value) in wanted:
            result[id(value)] = kind
            wanted.remove(id(value))
        elif isinstance(value, dict):
            pending.extend((item, _kind(key) or kind) for key, item in value.items()
                           if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend((item, kind) for item in value if isinstance(item, (dict, list)))
    return result


def _html_context(entry: html.HtmlElement) -> str | None:
    if entry.xpath("ancestor-or-self::*[self::nav or self::menu or self::footer or "
                   "@role='navigation' or @role='menu' or @role='search']"):
        return "navigation or search form"
    for node in (entry, *entry.iterancestors()):
        attrs = " ".join(node.get(key, "") for key in ("class", "id", "itemtype", "aria-label"))
        if kind := _kind(attrs):
            return kind
        if node.tag in {"main", "section", "body"}:
            headings = node.xpath("./h1 | ./header/h1")
            if headings and (kind := _kind(headings[0].text_content())):
                return kind
        if node.tag == "table" and any(re.search(
            r"deadline|contracting authority|teilnahmefrist|angebotsfrist", heading.text_content(), re.I
        ) for heading in node.xpath("./thead//th")):
            return "notice"
    return None


def _source_shows(source: Any, candidates: list[str]) -> bool:
    if not candidates:
        return False
    if isinstance(source, html.HtmlElement):
        text = " ".join([source.text_content(), *source.xpath("descendant-or-self::*/@*")])
    else:
        text = json.dumps(source, ensure_ascii=False)
    text = " ".join(text.split())
    return any(" ".join(value.split()) in text for value in candidates)


def rejection_reason(
    entry: Any, source: Any, values: dict, *, collection_kind: str | None, list_url: str,
    record_url: str, detail_url: str | None, observed_file: bool,
) -> str | None:
    """Return why a selected record cannot be verified as an individual notice."""
    identifier, title = values.get("id"), values.get("title")
    route = _route_kind(record_url, record=True) if record_url != list_url else None
    context = collection_kind
    if isinstance(entry, html.HtmlElement):
        context = _html_context(entry)
        # The reader may have selected a directory link as a document or supplied
        # the listing URL as fields.url. Check the source title's own target too.
        title_text = " ".join(title.split()) if isinstance(title, str) else None
        for anchor in entry.iter("a"):
            if not title_text or not anchor.get("href"):
                continue
            labels = [anchor.text_content(), *anchor.xpath(".//h1/text() | .//h2/text() | .//h3/text()")]
            if any(" ".join(label.split()) == title_text for label in labels) and (
                kind := non_notice_target(anchor.get("href"))
            ):
                return kind
    if detail_url and isinstance(source, html.HtmlElement):
        for node in source.xpath("descendant-or-self::body | descendant-or-self::main | descendant-or-self::article")[:3]:
            kind = _kind(" ".join(node.get(key, "") for key in ("class", "itemtype", "aria-label")))
            if kind and kind != "notice":
                return kind
            context = context or kind
    if isinstance(source, dict):
        for key in ("@type", "__typename", "recordType", "entityType", "type"):
            if isinstance(label := source.get(key), str) and (kind := _kind(label)):
                if kind != "notice":
                    return kind
                context = context or "notice"
    if route and route != "notice":
        return route
    if detail_url and (kind := non_notice_target(detail_url)):
        return kind
    if context and context != "notice":
        # An explicit notice permalink can identify a notice in a buyer's own
        # directory, but a menu or form is not a results list.
        if route != "notice" or context == "navigation or search form":
            return context
    if route == "notice" or context == "notice":
        return None
    listing = _route_kind(list_url)
    if listing and listing != "notice":
        return listing
    if not isinstance(identifier, str) or not isinstance(title, str):
        return None  # Missing/ambiguous fields have separate normalization and health diagnostics.
    if listing == "notice" or observed_file:
        return None
    metadata = [value for key in _SOURCE_FIELDS if isinstance(value := values.get(key), str)
                and value not in (identifier, title)]
    if _NOTICE.search(title):
        metadata.append(title)
    if _source_shows(source, metadata):
        return None
    if isinstance(source, dict) and any(_words(key) in _NOTICE_KEYS and value for key, value in source.items()):
        return None
    if isinstance(source, html.HtmlElement) and source.xpath(
        "descendant-or-self::*[@data-notice-id or @data-tender-id or @data-tendersid]"
    ):
        return None
    return "no corroborating evidence of an individual procurement notice"
