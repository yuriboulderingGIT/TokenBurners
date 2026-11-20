"""Berlin's public listing delegates document delivery to RIB Tender."""

import json
import re
from urllib.parse import urlsplit

from lxml import etree, html

_LIST_PATH = "/vergabeplattform/veroeffentlichungen/bekanntmachungen/"
_RIB_DETAIL = "https://meinauftrag.rib.de/public/DetailsByPlatformIdAndTenderId/platformId/2/tenderId/"
_RIB_FILES = re.compile(r"https://my\.vergabeplattform\.berlin\.de/remote/download\.php\?k=[0-9a-fA-F]{32}")
_DOCUMENT_DATA = re.compile(r"(?m)^\s*var\s+documents(?:Notices|Attachments)\s*=\s*(?=\[)")


def detail_url(listing_url: str, row: html.HtmlElement, extracted_id: object) -> str | None:
    """Use the detail URL published on this notice, with its ID corroborated by the card."""
    listing = urlsplit(listing_url)
    if listing.netloc != "www.berlin.de" or listing.scheme != "https" or listing.path != _LIST_PATH:
        return None
    cards = row.xpath("descendant-or-self::article[@data-id]")
    if len(cards) != 1:
        return None
    [card] = cards
    notice_id = card.get("data-id", "")
    if extracted_id != notice_id or not re.fullmatch(r"[0-9]{1,20}", notice_id):
        return None
    target = _RIB_DETAIL + notice_id
    return target if target in card.xpath(".//button/@data-href") else None


def document_urls(page: html.HtmlElement) -> list[str]:
    """Read the document grid's JSON data without evaluating the page's scripts."""
    links: dict[str, None] = {}
    for script in page.xpath("//script[not(@src)]/text()"):
        for marker in _DOCUMENT_DATA.finditer(script):
            try:
                rows, _ = json.JSONDecoder().raw_decode(script, marker.end())
            except (ValueError, RecursionError):
                continue
            if not isinstance(rows, list):
                continue
            pending = rows[::-1]
            while pending:
                row = pending.pop()
                if not isinstance(row, dict):
                    continue
                cells = row.get("data")
                if isinstance(cells, list) and cells and isinstance(cells[0], dict):
                    value = cells[0].get("value")
                    if isinstance(value, str) and "<a " in value:
                        try:
                            fragment = html.fragment_fromstring(value, create_parent=True)
                        except (ValueError, etree.ParserError):
                            continue
                        for href in fragment.xpath(".//a/@href"):
                            if _RIB_FILES.fullmatch(href):
                                links[href] = None
                children = row.get("rows")
                if isinstance(children, list):
                    pending.extend(reversed(children))
    return list(links)
