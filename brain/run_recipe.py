"""Read tenders and document links from a saved recipe without model calls."""

import copy
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from itertools import islice
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlencode, urldefrag, urljoin, urlsplit

import httpx
from jsonpath_ng import parse as parse_jsonpath
from jsonpath_ng.exceptions import JSONPathError
from lxml import etree, html

from app.fetcher import PortalFetcher, RunPageCache
from app.models import RawTender, Recipe
from brain import berlin_documents, charite_documents
from brain.blocks import Blocked, detect_block
from brain.notice_evidence import json_collection_kinds, non_notice_target, rejection_reason
from brain.notice_fields import fill_notice_fields, listed_deadline_column, notice_facts
from brain.normalize import _single_text, normalize
from brain.shrink import shrink

MAX_PAGES = 100
log = logging.getLogger(__name__)
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_STALE_ENDPOINT_STATUSES = {400, 405, 422}
_NEXT_PAGE_LABELS = {
    "next page", "nächste seite", "volgende pagina", "page suivante",
    "siguiente página", "página siguiente", "pagina successiva", "następna strona",
    "página seguinte", "próxima página", "aller à la page suivante",
    "další stránka", "ďalšia strana", "következő oldal", "επόμενη σελίδα",
    "pagina următoare", "pagina urmatoare", "strona następna", "strona nastepna",
    "sljedeća stranica", "sljedeca stranica", "naslednja stran", "naslednja stranica",
    "neste side", "nästa sida", "naesta sida", "seuraava sivu", "järgmine leht",
    "jaunu lapa", "kitas puslapis", "следваща страница",
}
_NEXT_LABELS = {
    "next", "weiter", "nächste", "nächster", "volgende", "suivant", "suivante",
    "siguiente", "successiva", "successivo", "następna", "dalej", "seguinte", "próxima",
    "další", "ďalej", "ďalšia", "következő", "επόμενο", "επόμενη", "următoarea", "urmatoarea",
    "naslednja", "sljedeća", "sljedeca", "neste", "nästa", "naesta", "seuraava", "järgmine",
    "jauns", "kitas", "следваща",
}
_PAGE_QUERY_KEYS = {
    "p", "page", "pagenum", "pagenumber", "pageindex", "offset", "skip", "start", "cursor", "seite", "pagina",
    "strana", "stronica", "strona", "sivu", "leht", "lapa", "puslapis",
}
_OFFSET_QUERY_KEYS = {"offset", "skip", "start"}
_TOTAL_KEYS = {"total", "totalcount", "nbtotal", "totalelements", "totalresults", "totalrecords", "totalhits", "nbhits"}
_PAGER_CONTEXT = re.compile(
    r"\b(?:pagination|paginator|paging|pager|page navigation|paginaci[oó]n|paginazione|"
    r"paginering|paginacja|str[aá]nkov[aá]n[ií]|lapoz[aá]s|σελιδοπο[iί]ηση|"
    r"bl[aá]ttere|sivutus|puslapiavimas)\b", re.I,
)
_PAGE_PATH_SUFFIX = re.compile(r"/(?:page|seite|pagina)/[0-9]+/?$", re.I)
_FILE_SUFFIX = re.compile(r"\.(?:pdf|zip|docx?|xlsx?|pptx?|odt|ods|rtf|csv|rar|7z)$", re.I)
_FILE_LABEL = re.compile(
    r"(?:\S+\.(?:pdf|zip|docx?|xlsx?|pptx?|odt|ods|rtf|csv|rar|7z)|\b(?:notice|document|attachment) PDF)$",
    re.I,
)
_EZAK_NOTICE = re.compile(r"/contract_display_[0-9]+\.html$")
_EZAK_FILE = re.compile(r"/document_([0-9]+)/[^/]+$")
_EZAK_DOCUMENT_PAGE = re.compile(r"/document_download_([0-9]+)\.html$")

Selector = Callable[[Any], list[Any]]
Origin = tuple[str, str, int]


class RecipeReadError(ValueError):
    """A page or selector prevents the recipe from being read reliably."""


class ListingFieldMismatch(RecipeReadError):
    """Optional listing summaries disagreed with the official notice during sampling."""


class _PageGone(RecipeReadError):
    """The endpoint returned HTTP 404 or 410."""


class NotANotice(RecipeReadError):
    """The selected entries represent another resource or lack evidence of a notice."""


class NoPublicNextLink(RecipeReadError):
    """An enabled next-page control cannot be followed by the HTTP reader."""


class PaginationError(RecipeReadError):
    """The observed page chain is incomplete. Discard its rows and repair the recipe."""

    def __init__(self, message: str, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.page_sample = (
            {"url": str(response.url), "html": shrink(response.text, max_chars=16_000)}
            if response is not None else None
        )


class MissedNextPage(PaginationError):
    """The recipe missed a next link that the listing itself advertises."""


class PageLimitExceeded(PaginationError):
    """The list goes on past MAX_PAGES. No recipe can fix that, so neither attach nor repair asks a model.

    Reading only the first pages is not an option: tenders beyond them would be marked closed.
    """

    def __init__(self, rows_read: int, advertised: int = 0, estimated_pages: int = 0) -> None:
        self.rows_read, self.advertised, self.estimated_pages = rows_read, advertised, estimated_pages
        size = []
        if estimated_pages > MAX_PAGES:
            size.append(f"about {estimated_pages} pages advertised")
        if advertised:
            size.append(f"{advertised:,} results advertised")
        size.append(f"{rows_read:,} rows on the first {MAX_PAGES} pages")
        super().__init__(
            f"the tender list is longer than the {MAX_PAGES}-page limit ({', '.join(size)}). Reading only part "
            "of it would wrongly close the other tenders. Add the portal with a narrower list URL, for example "
            "open tenders only, one buyer or one region"
        )

    def with_estimate(self, pages: int) -> "PageLimitExceeded":
        return PageLimitExceeded(self.rows_read, self.advertised, pages)


class ReadRows(list):
    """Rows plus an explicit empty-result signal from an authoritative JSON count."""

    verified_empty: bool = False


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
    index_link = _selector(recipe.kind, recipe.documents.page, "documents.page")
    attachment_owners: dict[str, str] = {}
    listing_fields = {k: _selector(recipe.kind, v, f"list.fields.{k}") for k, v in recipe.list_.fields.items()}
    scan_archive = recipe.kind == "html" and not recipe.list_.search and bool(
        {"submissionDeadline", "status"} & listing_fields.keys())
    if scan_archive and max_tenders is None and not isinstance(fetch, RunPageCache):
        wrapped = RunPageCache(fetch.fetcher, fetch.portal_id)
        wrapped.progress = getattr(fetch, "progress", None)
        fetch = wrapped
    if scan_archive and max_tenders is None and page_url not in fetch.scanned_pages:
        await run_recipe(recipe, fetch, max_tenders=3, verify_all_pages=True)
    large_archive = scan_archive and isinstance(fetch, RunPageCache) and fetch.scanned_pages.get(page_url, 0) >= 4
    resume = large_archive and max_tenders is None and getattr(fetch, "staged_notice", None)
    recipe_digest = hashlib.sha256(recipe.model_dump_json(by_alias=True).encode()).digest() if resume else b""
    checkpoint = getattr(fetch, "progress", None)

    tenders: list[RawTender] = ReadRows()
    seen_pages: set[str] = set()
    seen_rows: set[bytes] = set()
    identities: set[str] = set()
    fetched_links: set[str] = set()
    unresolved_links: set[str] = set()  # unvisited validation rows or details removed at source
    anonymous = 0  # rows without an identifier: distinct rows, the health check judges their ID rate
    checked = 0  # rows whose notice evidence was examined
    rejected: list[str] = []  # why examined rows are not individual notices; they are left out
    observed_count = advertised_count = 0
    sampled = skipped = 0
    search_pages = None
    scanned_page_urls: list[str] = []
    if recipe.list_.search:
        from brain.public_search import SearchPages
        search_pages = await SearchPages.open(page_url, recipe.list_.search, fetch)
    page_limit = 1000 if scan_archive and recipe.list_.max_pages == MAX_PAGES else recipe.list_.max_pages
    for page_number in range(1, page_limit + 1):
        if search_pages:
            page = await search_pages.read(page_number, observed_count)
            advertised_count = search_pages.total
            current_url = page_url
            sample_page = None
        else:
            if page_url in seen_pages:
                raise PaginationError("pagination revisited a list page")
            response = await fetch_page(fetch, page_url, origin)
            current_url = str(response.url)
            if current_url in seen_pages:
                raise PaginationError("pagination redirected to a previous list page")
            seen_pages.update((page_url, current_url))
            if current_url not in scanned_page_urls:
                scanned_page_urls.append(current_url)
            page = _parse_page(response, recipe.kind)
            sample_page = response if recipe.kind == "html" else None
        link_base = document_base(page, current_url)
        entries = [entry for entry in _flatten(item(page)) if entry is not None]
        if sample_page is not None:
            _check_block(sample_page, content_available=bool(entries))
            # Different URLs may render the very same notice cards with a new pager.
            if entries and all(isinstance(entry, html.HtmlElement) for entry in entries):
                rows_hash = hashlib.sha256()
                for entry in entries:
                    rows_hash.update(html.tostring(entry, with_tail=False))
                digest = rows_hash.digest()
                if digest in seen_rows:
                    raise PaginationError("the next listing page repeated the same notice rows", sample_page)
                seen_rows.add(digest)
        elif recipe.kind == "json_api" and entries and not search_pages:
            digest = hashlib.sha256(json.dumps(entries, sort_keys=True, default=str).encode()).digest()
            if digest in seen_rows:
                raise PaginationError("the next JSON page repeated the same entries")
            seen_rows.add(digest)

        if not entries and page_number > 1:
            if search_pages and observed_count >= advertised_count:
                break
            raise PaginationError(f"list.item found no tenders on page {page_number}", sample_page)
        observed_count += len(entries)
        advertised_count = max(advertised_count, _advertised_total_count(page))
        nav_page = navigation_view(page, current_url)
        if search_pages:
            following = page_url if search_pages.more(entries, observed_count) else None
        elif isinstance(page, html.HtmlElement) and _declared_last_page(nav_page, current_url):
            following = None
        elif isinstance(page, html.HtmlElement) and (pager := _numbered_pager(nav_page, current_url)) is not None:
            position, links = pager
            following = links.get(position + 1)
        else:
            try:
                advertised, get_forms = _advertised_pagination(nav_page, current_url)
                if advertised == get_forms and len(get_forms) == 1:
                    following = next(iter(get_forms))
                else:
                    selected = next_page(nav_page)
                    if (isinstance(page, html.HtmlElement) and not advertised and selected
                            and _last_numbered_page(nav_page, current_url)):
                        if all(link.lower().replace(" ", "") in {"javascript:void(0);", "javascript:void(0)", "#"}
                               for link in _strings(selected, links=True)):
                            selected = []
                    following = _navigation_url(
                        current_url, selected, origin, "list.next_page", source=nav_page, advertised=advertised,
                    )
            except RecipeReadError as exc:
                raise PaginationError(str(exc), sample_page) from exc
            if advertised and following not in advertised:
                raise MissedNextPage(
                    f"list.next_page missed the advertised next page on page {page_number}", sample_page
                )
            if (recipe.kind == "json_api" and recipe.list_.next_page is None and entries
                    and observed_count < advertised_count):
                following = _next_query_page(current_url, len(entries))
        if following is None and observed_count < advertised_count:
            raise PaginationError(
                f"the {'JSON' if recipe.kind == 'json_api' else 'HTML'} list advertised {advertised_count} tenders, but pagination exposed "
                f"only {observed_count} entries"
            )
        if not entries:
            tenders.verified_empty = (
                recipe.kind == "json_api" and page_number == 1
                and _advertised_total_count(page) == 0
            )
            if not hasattr(fetch, "recipe_pages"):
                fetch.recipe_pages = {}
            fetch.recipe_pages[recipe.list_.url] = scanned_page_urls
            return tenders

        collections = json_collection_kinds(page, entries) if recipe.kind == "json_api" else {}
        if checkpoint and (page_number == 1 or page_number % 5 == 0):
            checkpoint(f"{'Scanning listing' if verify_all_pages else 'Reading notices'}: page {page_number}, {observed_count} entries")
        deadline_columns = {}
        for entry in entries:
            sampling_done = max_tenders is not None and sampled >= max_tenders
            if sampling_done and not verify_all_pages:
                break
            source = entry
            tender_url = current_url
            source_base = link_base
            detail_url = None
            summary = {k: _field_value(select(entry)) for k, select in listing_fields.items()}
            checkpoint_key = None
            if resume and isinstance(entry, html.HtmlElement):
                checkpoint_key = hashlib.sha256(recipe_digest + current_url.encode() +
                                                html.tostring(entry, with_tail=False)).hexdigest()
                if cached := fetch.staged_notice(checkpoint_key):
                    tenders.append(cached)
                    if identifier := _single_text(cached.fields.get("id")):
                        identities.add(identifier)
                    else:
                        anonymous += 1
                    checked += 1
                    continue
            if large_archive and max_tenders is None and summary:
                row_url = (_navigation_url(link_base, detail(entry), origin, "detail.link", source=entry)
                           if recipe.detail.link else _title_link(entry, summary.get("title"), link_base, origin, []))
                raw_summary = RawTender(url=row_url or current_url, fields=summary, archived_summary=True)
                # An observed permalink lets the scan count a closed row even if its actual ID
                # is only on the detail. Do not invent a published identifier for that row.
                listed_tender = normalize(raw_summary.model_copy(update={"fields": {**summary, "id": summary.get("id") or row_url}}), recipe.locale)
                if row_url and listed_tender and listed_tender.status in {"withdrawn", "closed"}:
                    if summary.get("id") and summary.get("title"):
                        identities.add(listed_tender.id)
                        tenders.append(raw_summary)
                    else:
                        unresolved_links.add(row_url)
                    checked += 1
                    continue
            if recipe.detail.link is not None:
                link = _navigation_url(
                    link_base, detail(entry), origin, "detail.link", source=entry
                )
                if link is None:
                    raise RecipeReadError("detail.link found no URL for a list entry")
                if reason := non_notice_target(link):
                    checked += 1
                    rejected.append(f"detail.link points to {reason} instead of an individual procurement notice")
                    continue
                if sampling_done:
                    # A candidate can check the remaining list links without opening more details.
                    unresolved_links.add(link)
                    checked += 1
                    continue
                sampled += 1
                try:
                    detail_response = await fetch_page(fetch, link, origin)
                except _PageGone:
                    skipped += 1
                    unresolved_links.add(link)
                    continue
                fetched_links.add(link)
                tender_url = str(detail_response.url)
                detail_url = tender_url
                detail_is_html = _is_html_response(detail_response)
                source = _parse_page(detail_response, "html" if detail_is_html else recipe.kind)
                source_base = document_base(source, tender_url)
                if not recipe.documents.page and isinstance(source, html.HtmlElement) and (target := full_notice_url(source, source_base, origin)):
                    _check_block(detail_response)
                    detail_response = await fetch_page(fetch, target, origin)
                    source = _parse_page(detail_response, "html")
                    tender_url = detail_url = str(detail_response.url)
                    source_base = document_base(source, tender_url)
            elif not sampling_done:
                sampled += 1

            field_source = entry if recipe.kind == "json_api" and detail_url and detail_is_html else source
            values = {}
            for name, selector in fields.items():
                try:
                    values[name] = _field_value(selector(field_source))
                except (ValueError, TypeError, KeyError):
                    values[name] = None
            if detail_url and isinstance(source, html.HtmlElement):
                # Independently read the labelled official ID from the notice itself. A selector
                # can return a plausible value from the wrong table or a database row key.
                facts = notice_facts(source)
                selected = _single_text(values.get("id"))
                labelled = facts.get("id", (None, None))[0]
                if selected and labelled:
                    selected_identity = " ".join(selected.split()).casefold()
                    labelled_identity = " ".join(labelled.split()).casefold()
                    if selected.startswith(("/", "http://", "https://")):
                        path_id = unquote(urlsplit(selected).path.rstrip("/").rsplit("/", 1)[-1])
                        if " ".join(path_id.split()).casefold() == labelled_identity:
                            selected_identity = labelled_identity
                    if selected_identity != labelled_identity:
                        raise ListingFieldMismatch(
                            f"fields.id disagrees with the labelled value on the notice at {detail_url}: "
                            f"{selected!r}/{labelled!r}"
                        )
                fill_notice_fields(values, facts)
                title = _single_text(values.get("title"))
                if title and ":" in title and isinstance(entry, html.HtmlElement):
                    suffix = title.partition(":")[2].strip()
                    if any(" ".join(a.text_content().split()) == suffix for a in entry.xpath(".//a[@href]")):
                        values["title"] = suffix
            for key, value in summary.items():
                if not values.get(key):
                    values[key] = value
            if summary and detail_url and max_tenders is not None:
                primary = normalize(RawTender(url=detail_url, fields=values), recipe.locale)
                listed = normalize(RawTender(url=detail_url, fields={**summary, "id":summary.get("id") or detail_url}), recipe.locale)
                if primary and listed and (summary.get("id") and primary.id != listed.id or
                        (listed.submissionDeadline and primary.submissionDeadline and
                         listed.submissionDeadline != primary.submissionDeadline) or
                        listed.status in {"closed", "withdrawn"} and listed.status != primary.status):
                    raise ListingFieldMismatch(f"list.fields disagrees with the notice at {detail_url}: "
                                               f"ID {listed.id!r}/{primary.id!r}, "
                                               f"deadline {listed.submissionDeadline}/{primary.submissionDeadline}, "
                                               f"status {listed.status}/{primary.status}")
            if isinstance(entry, html.HtmlElement) and entry.tag == "tr" and not values.get("submissionDeadline"):
                table = next(entry.iterancestors("table"), None)
                if table not in deadline_columns:
                    deadline_columns[table] = listed_deadline_column(entry)
                index = deadline_columns[table]
                cells = entry.xpath("./td")
                if index is not None and index < len(cells) and not entry.xpath("./td[@rowspan or @colspan]"):
                    values["submissionDeadline"] = " ".join(cells[index].text_content().split()) or None
            if identifier := _single_text(values.get("id")):
                identities.add(identifier)
            else:
                anonymous += 1
            file_source, file_base = source, source_base
            if recipe.documents.page:
                if not detail_url or not isinstance(source, html.HtmlElement):
                    raise RecipeReadError("documents.page requires an individual HTML notice")
                index_url = _navigation_url(source_base, index_link(source), origin, "documents.page", source=source)
                if not index_url or index_url in {current_url, detail_url}:
                    raise RecipeReadError("documents.page did not identify a separate attachment index on the notice")
                index_response = await fetch_page(fetch, index_url, origin)
                _check_block(index_response)
                final_index = str(index_response.url)
                if attachment_owners.setdefault(final_index, detail_url) != detail_url:
                    raise RecipeReadError("documents.page sends different notices to the same attachment index")
                file_source = _parse_page(index_response, "html")
                file_base = document_base(file_source, final_index)
            if isinstance(file_source, html.HtmlElement) and (detail_url or recipe.documents.tab):
                file_source = _document_panel(file_source, recipe.documents.tab)
            document_selector = documents
            if isinstance(file_source, html.HtmlElement) and recipe.kind == "json_api" and recipe.documents.link:
                document_selector = _selector("html", recipe.documents.link, "documents.link")
            document_urls = list(dict.fromkeys(
                url
                for value in _strings(document_selector(file_source), links=True)
                if (url := _resolve_http_url(file_base, value)) is not None
                and _observed_href(file_source, file_base, url, document_only=True)
            ))
            if recipe.documents.page:
                document_urls.extend(u for u in _attachment_links(file_source, file_base) if portal_origin(u) == origin)
                if not document_urls:
                    raise RecipeReadError("documents.link found no files on the notice's attachment index")
            if recipe.kind == "html" and recipe.detail.link is not None:
                _check_block(
                    detail_response, content_available=bool(values.get("id") or document_urls)
                )
            observed_file = any(_FILE_SUFFIX.search(urlsplit(url).path) for url in document_urls)
            if isinstance(source, html.HtmlElement):
                # A notice with a separate dossier may also link supplier questions.
                attachments = [] if recipe.documents.page else _attachment_links(file_source, source_base)
                observed_file = bool(attachments)
                if search_pages:
                    # A search row's other links often lead to profiles and navigation.
                    document_urls = [url for url in document_urls if url in attachments]
                public_files = [url for url in attachments if portal_origin(url) == origin]
                if source is not entry and _EZAK_NOTICE.fullmatch(urlsplit(tender_url).path):
                    document_urls = _prefer_ezak_file_links(document_urls, public_files)
                document_urls.extend(public_files)
            if source is not entry and isinstance(entry, html.HtmlElement) and not (recipe.documents.page or recipe.documents.tab):
                row_attachments = _attachment_links(entry, link_base)
                observed_file = observed_file or bool(row_attachments)
                document_urls.extend(url for url in row_attachments if portal_origin(url) == origin)
            if isinstance(values.get("url"), str) and not recipe.documents.page:
                # Public feeds may link to a separate notice website. This value is
                # delivered as metadata only, after all fetch destinations are chosen.
                if recipe.kind == "json_api":
                    tender_url = _resolve_http_url(source_base, values["url"]) or tender_url
                else:
                    tender_url = resolve_portal_url(source_base, values["url"], origin) or tender_url
            if recipe.detail.link is None and tender_url == current_url and isinstance(entry, html.HtmlElement):
                tender_url = _title_link(entry, values.get("title"), link_base, origin, document_urls) or current_url
                if search_pages and tender_url == current_url and isinstance(values.get("id"), str):
                    matches = set()
                    for href in entry.xpath(".//a[not(@download)]/@href"):
                        url = resolve_portal_url(link_base, href, origin)
                        if url and url not in document_urls:
                            parts = urlsplit(url)
                            if (parts.path.rstrip("/").rsplit("/", 1)[-1] == values["id"]
                                    or [values["id"]] in parse_qs(parts.query).values()):
                                matches.add(url)
                    if len(matches) == 1:
                        tender_url = matches.pop()
            record_url = tender_url
            if (isinstance(source, html.HtmlElement) and values.get("url") and tender_url != detail_url
                    and tender_url != current_url and not _observed_href(source, source_base, tender_url)):
                record_url = detail_url or current_url
            reason = rejection_reason(
                entry, source, values, collection_kind=collections.get(id(entry)), list_url=current_url,
                record_url=record_url, detail_url=detail_url, observed_file=observed_file,
            )
            checked += 1
            if reason:
                # One odd row (a banner, a category teaser) is left out; most rows decide below.
                rejected.append(f"selected record is not a verified procurement notice: {reason}")
                continue
            if sampling_done:
                continue
            if (
                recipe.detail.link is None and isinstance(entry, html.HtmlElement)
                and _EZAK_NOTICE.fullmatch(urlsplit(tender_url).path) and isinstance(values.get("id"), str)
                and _observed_href(entry, link_base, tender_url)
            ):
                # E-ZAK exposes the files on the observed notice rather than in its listing.
                # Keep the list row as the authority for the tender's fields.
                try:
                    detail_response = await fetch_page(fetch, tender_url, origin)
                except _PageGone:
                    skipped += 1
                    continue
                _check_block(detail_response)
                detail_url = str(detail_response.url)
                if urlsplit(detail_url).path != urlsplit(tender_url).path:
                    raise RecipeReadError("the E-ZAK document page redirected away from the observed tender")
                detail_doc = _parse_page(detail_response, "html")
                detail_base = document_base(detail_doc, detail_url)
                document_urls.extend(
                    url for url in _attachment_links(detail_doc, detail_base) if portal_origin(url) == origin
                )
            if isinstance(entry, html.HtmlElement) and (answers := charite_documents.answers_url(
                current_url, entry, values.get("id"),
            )):
                document_urls.append(answers)
            if isinstance(entry, html.HtmlElement) and (delegate := berlin_documents.detail_url(
                current_url, entry, values.get("id"),
            )):
                tender_url = delegate
                try:
                    detail_response = await fetch_page(fetch, delegate, portal_origin(delegate))
                    if not urlsplit(str(detail_response.url)).path.startswith("/public/publications/"):
                        raise RecipeReadError("Berlin detail did not open a public RIB notice")
                    detail_doc = _parse_page(detail_response, "html")
                    document_urls.extend(berlin_documents.document_urls(detail_doc))
                except (Blocked, RecipeReadError, httpx.HTTPError) as exc:
                    # The listing remains readable when one delegated detail is unavailable.
                    log.info("Berlin document source unavailable: %s", exc)
            # A tender's own page is not one of its documents (tenderned.nl: a recipe mapped both to the
            # notice link, and every read would "download" thousands of web pages as failed files).
            document_urls = [url for url in dict.fromkeys(document_urls) if url != tender_url]
            if (not document_urls and isinstance(source, html.HtmlElement)
                    and next(_document_controls(source), None) is not None):
                raise RecipeReadError("the notice shows a document download button, but no public file link was found")
            tenders.append(RawTender(url=tender_url, detail_url=detail_url, fields=values,
                                     source_language=(source.getroottree().getroot().get("lang")
                                                      if isinstance(source, html.HtmlElement) else None),
                                     document_links=document_urls))
            if checkpoint_key:
                fetch.stage_notice(checkpoint_key, tenders[-1])
        if following is None or (not verify_all_pages and max_tenders == sampled):
            break
        page_url = following
    else:
        if recipe.list_.max_pages >= MAX_PAGES:
            raise PageLimitExceeded(observed_count, advertised_count)
        raise PaginationError("pagination exceeded the recipe's max_pages")
    if rejected and 2 * len(rejected) >= checked:
        raise NotANotice(f"{rejected[0]} ({len(rejected)} of {checked} rows)")
    if rejected:
        log.info("%s: left out %d of %d rows: %s", recipe.list_.url, len(rejected), checked, rejected[0])
    if advertised_count and (max_tenders is None or verify_all_pages):
        # Offset and page requests still advance by physical rows. Only distinct
        # portal IDs can establish coverage on a full read, even if rows overlap.
        unique_count = len(identities) + anonymous + len(unresolved_links - fetched_links)
        if unique_count < advertised_count:
            raise PaginationError(f"the listing advertised {advertised_count} tenders, but pagination exposed "
                                  f"{unique_count} distinct identities in {observed_count} entries")
    if skipped:
        message = f"{skipped} of {sampled} tender detail pages returned HTTP 404 or 410"
        log.info("%s: %s", recipe.list_.url, message)
        # Tolerate one removed notice, or up to 20% on larger listings.
        if not tenders or (skipped >= 2 and skipped * 5 > sampled):
            raise RecipeReadError(message)
    if verify_all_pages and isinstance(fetch, RunPageCache):
        fetch.scanned_pages[recipe.list_.url] = page_number
    if not hasattr(fetch, "recipe_pages"):
        fetch.recipe_pages = {}
    fetch.recipe_pages[recipe.list_.url] = scanned_page_urls
    if recipe.detail.rendered:
        if recipe.kind != "json_api" or recipe.detail.link or not recipe.fields.get("url"):
            raise RecipeReadError("a rendered detail needs an individual notice URL from a JSON listing")
        selectors = {key: _selector("html", expr, "rendered detail " + key)
                     for key, expr in recipe.detail.rendered_fields.items()}
        for pos, raw in enumerate(tenders):
            if portal_origin(raw.url) != origin or raw.url == recipe.list_.url:
                raise RecipeReadError("the JSON record has no public notice URL on its portal")
            if checkpoint and pos % 5 == 0:
                checkpoint(f"Checking rendered notices: {pos + 1} of {len(tenders)}")
            source, files = await rendered_file_source(raw.url, fetch)
            for key, select in selectors.items():
                if value := _field_value(select(source)):
                    raw.fields[key] = value
            raw.document_links = list(dict.fromkeys([*raw.document_links, *files]))
            raw.document_names = files
            raw.detail_url = raw.url
    return tenders


async def rendered_file_source(url, fetch):
    from brain.js_discover import _capture_json
    # Reuse the three validated notices within this read. Rotating browser sessions on
    # later reads must observe current public URLs afresh.
    cache = getattr(fetch, "rendered_samples", None)
    if cache is None:
        cache = fetch.rendered_samples = {}
    if url in cache:
        return cache[url]
    notice = {}
    await _capture_json(url, fetch, notice=notice)
    if not notice.get("markup"):
        raise RecipeReadError("Chromium could not render the public tender notice")
    source = html.fromstring(notice["markup"])
    files = dict(notice["files"])
    for link in _attachment_links(source, document_base(source, url)):
        if portal_origin(link) == portal_origin(url):
            files.setdefault(link, "")
    if len(cache) < 3:
        cache[url] = (source, files)
    return source, files


def full_notice_url(source: html.HtmlElement, base_url: str, origin: Origin) -> str | None:
    """One explicitly labelled continuation from a summary to the full public notice."""
    targets = set()
    for anchor in source.xpath("//a[@href and not(@download) and not(ancestor::nav or ancestor::footer or ancestor::aside)]"):
        label = " ".join(anchor.text_content().casefold().split())
        if not re.fullmatch(
            r"(?:auf der |zur |zum |view (?:the )?|open (?:the )?|go to (?:the )?)?"
            r"(?:verfahrensseite|vollständige bekanntmachung|full (?:contract |tender )?notice|"
            r"tender details|complete notice|avis complet|volledige aankondiging)", label
        ):
            continue
        target = resolve_portal_url(base_url, anchor.get("href"), origin)
        if target and target != base_url:
            targets.add(target)
    return targets.pop() if len(targets) == 1 else None


def _document_controls(source: html.HtmlElement) -> Iterator[html.HtmlElement]:
    """Visible, actionable controls that explicitly offer the notice's documents."""
    for node in source.xpath(
        "descendant-or-self::*[self::button or self::a or @role='button' or self::input[@type='button']]"
        "[not(ancestor-or-self::nav or ancestor-or-self::footer or ancestor-or-self::aside "
        "or ancestor-or-self::*[@hidden or @aria-hidden='true' or @disabled or @aria-disabled='true' or @role='navigation'])]"
    ):
        label = " ".join(" ".join((node.text_content(), node.get("aria-label", ""), node.get("title", ""),
                                   node.get("value", ""))).casefold().split())
        if re.search(r"\b(?:download (?:the |all )?(?:(?:tender|procurement|contract) )?(?:documents?|attachments?)"
                     r"|(?:vergabeunterlagen|ausschreibungsunterlagen) herunterladen"
                     r"|télécharger (?:les |le )?(?:documents|dossier))\b", label):
            yield node


def _document_panel(source: html.HtmlElement, label: str | None) -> html.HtmlElement:
    tabs = source.xpath("descendant-or-self::*[@role='tab'][@aria-controls or starts-with(@href,'#')]")
    panels = {}
    for tab in tabs:
        name = " ".join(tab.text_content().split())
        target = tab.get("aria-controls") or tab.get("href", "")[1:]
        if name and target:
            panels[name] = target
    if not label and len(set(panels.values())) < 2:
        return source
    if not label or label not in panels:
        raise RecipeReadError("documents.tab must identify the notice's primary dossier among its tabs: "
                              + ", ".join(panels)[:300])
    nodes = source.xpath("descendant-or-self::*[@id=$target]", target=panels[label])
    if len(nodes) != 1:
        raise RecipeReadError(f"documents.tab {label!r} has no public dossier panel in the notice HTML")
    return nodes[0]


def _attachment_links(source: html.HtmlElement, base_url: str) -> list[str]:
    """Recover file links with positive evidence on the selected row or notice."""
    links = []
    for anchor in source.xpath(
        "descendant-or-self::a[@href]"
        "[not(ancestor::nav or ancestor::footer or ancestor::aside or ancestor::header[not(ancestor::article or ancestor::main)] or ancestor::*[@role='navigation'])]"
    ):
        url = _resolve_http_url(base_url, anchor.get("href", ""))
        if not url:
            continue
        labels = [anchor.text_content(), anchor.get("title", ""), *anchor.xpath(".//img/@alt | .//span/@title")]
        if (
            "download" in anchor.attrib or "enclosure" in anchor.get("rel", "").lower().split()
            or anchor.get("type", "").lower() in {"application/pdf", "application/zip", "application/octet-stream"}
            or _FILE_SUFFIX.search(urlsplit(url).path)
            or any(_FILE_LABEL.search(" ".join(label.split())) for label in labels)
        ):
            links.append(url)
    for control in _document_controls(source):
        target = control.get("data-download-url") or control.get("data-href") or control.get("href")
        if (target and not target.strip().startswith("#")
                and (url := resolve_portal_url(base_url, target, portal_origin(base_url))) and url != base_url):
            links.append(url)
    return links


def _prefer_ezak_file_links(selected: list[str], observed: list[str]) -> list[str]:
    """An E-ZAK document details page can sit beside the direct file for the same record."""
    direct_ids = {
        match[1] for url in observed if (match := _EZAK_FILE.search(urlsplit(url).path))
    }
    return [
        url for url in selected
        if not (match := _EZAK_DOCUMENT_PAGE.search(urlsplit(url).path)) or match[1] not in direct_ids
    ]


def _title_link(
    entry: html.HtmlElement, title: str | list[str] | None,
    base_url: str, origin: Origin, document_urls: list[str],
) -> str | None:
    """Recover a notice permalink from a list-only recipe when the title is itself a link."""
    if not isinstance(title, str) or not title.strip():
        return None
    label = " ".join(title.split())
    matches = set()
    for anchor in entry.iter("a"):
        if "download" in anchor.attrib or " ".join(anchor.text_content().split()) != label:
            continue
        url = resolve_portal_url(base_url, anchor.get("href", ""), origin)
        if not url or url == base_url or url in document_urls:
            continue
        if urlsplit(url).path.lower().endswith((".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx")):
            continue
        matches.add(url)
    return matches.pop() if len(matches) == 1 else None


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
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Check redirects before requesting them, under the same portal rate limit."""
    if portal_origin(url) != origin:
        raise RecipeReadError("page URL is outside the recipe's origin")
    for _ in range(_MAX_REDIRECTS + 1):
        response = await fetch.get(url, follow_redirects=False, **({"headers": headers} if headers else {}))
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


def _is_html_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").casefold()
    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        return True
    return response.content.lstrip(b"\xef\xbb\xbf \t\r\n").lower().startswith((b"<!doctype html", b"<html"))


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


def advertised_next_urls(page: Any, base_url: str) -> set[str]:
    """Independent evidence that treating this listing as the final page would lose rows."""
    return _advertised_pagination(page, base_url)[0]


def _advertised_pagination(page: Any, base_url: str) -> tuple[set[str], set[str]]:
    links = []
    form_urls = set()
    if isinstance(page, html.HtmlElement):
        for node in next_page_controls(page, base_url):
            href = node.get("href", "")
            links.append(href)
            if node.tag == "button" and not href and (target := _get_pager_url(node, base_url)):
                form_urls.add(target)
    elif isinstance(page, dict):
        # A JSON 'next' can also be an opaque cursor. Only explicit URL references count.
        for container in (page, page.get("links"), page.get("_links"), page.get("pagination")):
            if isinstance(container, dict):
                value = container.get("next")
                if isinstance(value, dict):
                    value = value.get("href")
                if isinstance(value, str) and value.startswith(("/", "?", "./", "../", "http://", "https://")):
                    links.append(value)
    urls = form_urls | {url for link in links if (url := _resolve_http_url(base_url, link))}
    if isinstance(page, html.HtmlElement) and links and not urls and not _last_numbered_page(page, base_url):
        raise NoPublicNextLink("the next page control provides no public HTTP link")
    return urls, form_urls


def _numbered_pager(page: html.HtmlElement, base_url: str) -> tuple[int, dict[int, str]] | None:
    """Recognize an observed sequence on this listing, with exactly the same search filters."""
    base = urlsplit(base_url)
    current = parse_qs(base.query, keep_blank_values=True)
    groups: dict[str, dict[int, tuple[int, str]]] = {}
    for anchor in page.xpath("//a[@href]"):
        label = anchor.text_content().strip()
        if not re.fullmatch(r"[1-9]\d{0,7}", label) or _is_disabled(anchor):
            continue
        url = resolve_portal_url(base_url, anchor.get("href"), portal_origin(base_url))
        if not url or urlsplit(url).path != base.path:
            continue
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        for key, values in query.items():
            if key.casefold() not in _PAGE_QUERY_KEYS or len(values) != 1 or not re.fullmatch(r"\d{1,10}", values[0]):
                continue
            if {k: v for k, v in query.items() if k != key} != {k: v for k, v in current.items() if k != key}:
                continue
            group = groups.setdefault(key, {})
            candidate = (int(values[0]), url)
            if group.setdefault(int(label), candidate) != candidate:
                return None
    for key, group in groups.items():
        if len(group) < 2:
            number, (value, url) = next(iter(group.items()))
            # A two-page pager often renders the current page as plain text and only links page 2.
            # Require a marked current page alongside the link before treating it as a sequence.
            pagers = page.xpath("//nav[.//*[@aria-current='page']] | //*[@role='navigation'][.//*[@aria-current='page']]"
                                " | //*[*[@aria-current='page'] and a[@href]]")
            for container in pagers:
                active = {" ".join(node.text_content().split()) for node in container.xpath(".//*[@aria-current='page']")}
                if (key.casefold() not in _OFFSET_QUERY_KEYS and number == value and active == {str(number - 1)}
                        and current.get(key, ["1"]) == [str(number - 1)]
                        and any(resolve_portal_url(base_url, href, portal_origin(base_url)) == url
                                for href in container.xpath(".//a/@href"))):
                    return number - 1, {number: url}
            continue
        pages = sorted(group)
        low, high = pages[0], pages[-1]
        start = group[low][0]
        step, remainder = divmod(group[high][0] - start, high - low)
        if remainder or step <= 0 or any(val != start + (n - low) * step for n, (val, _) in group.items()):
            continue
        first = start - (low - 1) * step
        if first not in (0, 1):
            continue
        value = current.get(key, [str(first)])
        if len(value) != 1 or not re.fullmatch(r"\d{1,10}", value[0]):
            continue
        position, remainder = divmod(int(value[0]) - first, step)
        position += 1
        if remainder or position < 1:
            continue
        if position < high and position + 1 not in group:
            raise PaginationError(f"the numbered pager advertises page {high} but has no link to page {position + 1}")
        if position >= high and next_page_controls(page, base_url):
            continue  # a forward control can extend beyond the visible numbers
        return position, {n: url for n, (_, url) in group.items()}
    return None


def _get_pager_url(button: html.HtmlElement, base_url: str) -> str | None:
    name = button.get("name", "")
    value = button.get("value", "")
    if name.casefold() not in _PAGE_QUERY_KEYS or not re.fullmatch(r"[0-9]{1,12}", value):
        return None
    if button.get("type", "submit").lower() != "submit" or any(
        key.startswith(("form", "on")) for key in button.attrib
    ):
        return None
    form = next(button.iterancestors("form"), None)
    if form is None:
        return None
    return _public_get_form(form, base_url, [(name, value)])


def empty_search_url(page: html.HtmlElement | None, base_url: str) -> str | None:
    """Find one explicitly public search form that can be sent without a keyword."""
    if page is None:
        return None
    candidates = {
        url for form in page.xpath("descendant-or-self::form[@role='search']")
        if (url := _public_get_form(form, base_url, [], empty_search=True)) and url != base_url
    }
    if len(candidates) == 1:
        return candidates.pop()
    path = urlsplit(base_url).path.rstrip("/") + "/"
    if path == "/":
        return None
    # Pages can also carry a site-wide search. Prefer the form under this listing's own path.
    local = {url for url in candidates if urlsplit(url).path.startswith(path)}
    return local.pop() if len(local) == 1 else None


def _public_get_form(
    form: html.HtmlElement, base_url: str, submit: list[tuple[str, str]], *, empty_search: bool = False,
) -> str | None:
    if form.get("method", "").lower() != "get" or any(key.startswith("on") for key in form.attrib):
        return None
    origin = portal_origin(base_url)
    target = resolve_portal_url(base_url, form.get("action", ""), origin) if origin else None
    if not target:
        return None
    found_search = False
    params = []
    for control in form.xpath(".//input|.//select|.//textarea|.//button"):
        if _is_disabled(control) or control.xpath("ancestor::fieldset[@disabled]"):
            continue
        if control.tag == "button" or control.get("type", "").lower() == "submit":
            if empty_search and any(key.startswith(("form", "on")) for key in control.attrib):
                return None
            continue
        if control.tag != "input" or "form" in control.attrib:
            return None
        name = control.get("name")
        kind = control.get("type", "text").lower()
        if kind == "hidden" and name:
            params.append((name, control.get("value", "")))
        elif empty_search and kind == "search" and name and "required" not in control.attrib:
            if control.get("value", "").strip() or control.get("minlength", "0") != "0":
                return None
            found_search = True
            params.append((name, ""))
        else:
            return None
    if empty_search and not found_search:
        return None
    url = urlsplit(target)._replace(query=urlencode(params + submit)).geturl()
    return url if len(url) <= 4096 else None


def _declared_last_page(page: html.HtmlElement, current_url: str) -> bool:
    """An explicit last-page URL can disambiguate an unlabeled forward arrow."""
    last_urls: set[str] = set()
    for node in page.xpath("//a[@rel and @href] | //link[@rel and @href]"):
        relations = node.get("rel", "").lower().split()
        if "next" in relations and _is_next_page_link(node, current_url):
            return False
        if "last" in relations:
            target = _resolve_http_url(current_url, node.get("href", ""))
            if target is not None:
                last_urls.add(target)
    return last_urls == {current_url}


def next_page_controls(page: html.HtmlElement, base_url: str) -> list[html.HtmlElement]:
    return [node for node in page.xpath(
        "descendant-or-self::a[@href] | descendant-or-self::button | "
        "descendant-or-self::link[@href and @rel]"
    ) if _is_next_page_link(node, base_url)]


def _last_numbered_page(page: html.HtmlElement, base_url: str) -> bool:
    # Some pagers leave a placeholder Next link on the last page. Confirm an
    # adjacent current/total counter and a working sibling link to earlier pages.
    for node in next_page_controls(page, base_url):
        container = node.getparent()
        if container is None or not any(
            _resolve_http_url(base_url, href) for href in container.xpath("./a/@href")
        ):
            continue
        for label in container.xpath("./text() | ./*[not(self::a) and not(self::button)]/text()"):
            match = re.search(r"(?<![\d.,])(\d+)\s*(?:/|[a-zA-ZÀ-ž]{1,6})\s*(\d+)(?![\d.,])", label)
            if match and int(match[1]) == int(match[2]) > 0:
                return True
    return False


def _is_next_page_link(node: html.HtmlElement, base_url: str) -> bool:
    context = [node, *islice(node.iterancestors(), 4)]
    if any(_is_disabled(control) for control in context):
        return False
    if "next" in node.get("rel", "").lower().split():
        return True
    labels = {
        " ".join(value.casefold().split()).strip(" ›»→←‹«.")
        for value in (node.text_content(), *node.xpath("descendant-or-self::*/@title | descendant-or-self::*/@aria-label"))
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
    if isinstance(page, html.HtmlElement):
        # Infinite-scroll lists sometimes compare an offset to a declared result
        # count instead of publishing a next URL. Do not publish the initial slice.
        for script in page.xpath("//script[not(@src)]/text()"):
            for field, total in re.findall(
                r"\b(?:var|const|let)\s+(total(?:Entries|Results|Count))\s*=\s*(\d{1,10})\s*;", script, re.I
            ):
                if re.search(r"\b(?:offset|start|loaded)\s*<\s*" + field + r"\b", script, re.I):
                    return int(total)
        return 0
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
        for key, value in container.items():
            if not isinstance(key, str) or re.sub(r"[^a-z]", "", key.lower()) not in _TOTAL_KEYS:
                continue
            if isinstance(value, str) and len(value) <= 18 and re.fullmatch(r"[0-9]+", value):
                value = int(value)
            if type(value) is int:
                total = max(total, value)
    return total


def _next_query_page(url: str, page_size: int) -> str | None:
    """Advance the one numeric paging parameter of a feed that states a total but links no next page."""
    parts = urlsplit(url)
    pairs = parts.query.split("&")
    paging = []
    for index, pair in enumerate(pairs):
        key, _, value = pair.partition("=")
        if re.sub(r"[^a-z]", "", unquote_plus(key).lower()) in _PAGE_QUERY_KEYS and value.isascii() and value.isdigit():
            paging.append((index, key, int(value)))
    if len(paging) != 1:
        return None
    index, key, value = paging[0]
    step = page_size if re.sub(r"[^a-z]", "", unquote_plus(key).lower()) in _OFFSET_QUERY_KEYS else 1
    pairs[index] = f"{key}={value + step}"
    return parts._replace(query="&".join(pairs)).geturl()


def _observed_href(source: Any, base_url: str, url: str, *, document_only: bool = False) -> bool:
    if not isinstance(source, html.HtmlElement):
        return True  # JSONPath selects values from the response itself.
    selector = ("descendant-or-self::a[not(ancestor::nav or ancestor::footer or ancestor::aside "
                "or ancestor::header[not(ancestor::article or ancestor::main)])]/@href"
                if document_only else "descendant-or-self::a/@href")
    return any(
        _resolve_http_url(base_url, href) == url
        for href in source.xpath(selector)
    )


def document_base(page: Any, url: str) -> str:
    """Relative links resolve against the page's <base href> when it declares one."""
    if isinstance(page, html.HtmlElement):
        for href in page.xpath("//base/@href")[:1]:
            return _resolve_http_url(url, href) or url
    return url


def navigation_view(page: Any, url: str) -> Any:
    """With a <base href>, a copy whose links and form actions are absolute.

    Pagination compares link targets with the page's own URL, so it reads this copy;
    list rows keep their original hrefs, which recipes may use as tender ids.
    """
    base = document_base(page, url)
    if base == url:
        return page
    view = copy.deepcopy(page)
    for node in view.xpath("//*[@href] | //form[@action]"):
        attribute = "action" if node.tag == "form" else "href"
        value = node.get(attribute, "").strip()
        # Empty actions submit to the page itself; placeholders stay placeholders.
        if value and not value.startswith("#") and not value.lower().startswith("javascript:"):
            if absolute := _resolve_http_url(base, value):
                node.set(attribute, absolute)
    return view


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
            value = value.get("href") if links else " ".join(value.xpath(
                "descendant-or-self::*/text()[not(ancestor::style or ancestor::script or ancestor::template "
                "or ancestor::noscript or ancestor::*[@hidden or @aria-hidden='true'])]"))
        elif isinstance(value, etree._ElementUnicodeResult) and not links and not value.is_attribute and not value.is_tail:
            parent = value.getparent()
            if parent is not None and parent.xpath("ancestor-or-self::*[self::style or self::script or self::template or self::noscript or @hidden or @aria-hidden='true']"):
                continue
        if not links and type(value) in (int, float):
            value = str(value)
        if isinstance(value, str) and (value := value.strip()):
            yield value


def _field_value(matches: list[Any]) -> str | list[str] | None:
    values = list(dict.fromkeys(_strings(matches)))
    if len(values) == 1:
        return values[0]
    return values or None
