"""Read explicitly labelled facts on an individual procurement notice."""

import re
from urllib.parse import unquote

from lxml import html


_LABELS = {
    "id": ("identifier", "tender id", "notice id", "kennung", "vergabenummer"),
    "buyerName": ("buyer", "contracting authority", "auftraggeber", "öffentlicher auftraggeber", "vergabestelle"),
    "submissionDeadline": ("submission deadline", "closing date", "angebotsfrist", "schlusstermin"),
    "publicationDate": ("publication", "published on", "publication date", "veröffentlicht am"),
    "referenceNumber": ("reference number", "ref.", "aktenzeichen", "az."),
    "procedureType": ("procedure type", "verfahrensart"),
    "contractNature": ("contract nature", "art des auftrags", "art"),
    "cpvCodes": ("cpv code", "cpv codes", "cpv"),
    "status": ("status", "verfahrensstatus", "tender status"),
    "placeOfPerformance": ("place of performance", "erfüllungsort"),
}
_KEY = {label: key for key, labels in _LABELS.items() for label in labels}


def listed_deadline_column(row: html.HtmlElement) -> int | None:
    """Use an unambiguous header on a simple results table as independent deadline evidence."""
    table = next(row.iterancestors("table"), None)
    if table is None:
        return None
    headers = table.xpath("./thead/tr[th] | ./tr[th] | ./tbody/tr[th]")
    if len(headers) != 1 or headers[0].xpath("./th[@colspan or @rowspan]"):
        return None
    indices = [n for n, th in enumerate(headers[0].xpath("./th"))
               if _KEY.get(" ".join(th.text_content().casefold().split()).rstrip(":")) == "submissionDeadline"
               or th.text_content().strip().casefold() == "deadline"]
    return indices[0] if len(indices) == 1 else None


def notice_facts(page: html.HtmlElement) -> dict[str, tuple[str, str]]:
    """Return unambiguous (source value, reusable selector) pairs."""
    facts = {}
    ambiguous = set()
    for node in page.xpath("//tr[count(th)=1 and count(td)=1]/th | //dl/dt[following-sibling::*[1][self::dd]]"):
        label = " ".join(node.text_content().split())
        key = _KEY.get(label.casefold().rstrip(":"))
        if not key:
            continue
        sibling = "td" if node.tag == "th" else "dd"
        target = node.getnext()
        if target is None or target.tag != sibling:
            continue
        value = " ".join(target.text_content().split())
        if not value:
            continue
        # Recognized labels contain no quotes. Recheck the same label on every notice.
        selector = f"//{node.tag}[normalize-space(.)='{label}']/following-sibling::{sibling}[1]"
        if key in facts and facts[key][0] != value:
            ambiguous.add(key)
        facts[key] = value, selector
    return {key: pair for key, pair in facts.items() if key not in ambiguous}


def fill_notice_fields(values, facts):
    for key, (value, _) in facts.items():
        if key == "placeOfPerformance":
            match = re.fullmatch(r"(\d{4,6})\s+([^,\d]+),\s*([A-Z]{2})", value)
            if match:
                for name, part in zip(("postalCode", "city", "countryCode"), match.groups()):
                    if not values.get("placeOfPerformance." + name):
                        values["placeOfPerformance." + name] = part.strip()
        elif key == "id":
            current = values.get("id")
            if (isinstance(current, str) and current.startswith(("/", "http://", "https://"))
                    and unquote(current.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]) == value):
                values["id"] = value
        elif not values.get(key):
            values[key] = value
