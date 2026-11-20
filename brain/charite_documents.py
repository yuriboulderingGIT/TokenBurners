"""Public bidder Q&A downloads on Charité's e-VA portal."""

import re
from urllib.parse import urlsplit

from lxml import html

_ANSWER_BUTTON = re.compile(r"cep_downloadAnswers\(([0-9]{1,20})\);?")
_PUBLIC_ANSWERS = "https://vergabeplattform.charite.de/download/cep_downloadAnswers.php?sid="


def answers_url(listing_url: str, row: html.HtmlElement, extracted_id: object) -> str | None:
    """Use the public download action only when its record matches the extracted tender."""
    listing = urlsplit(listing_url)
    if (listing.scheme, listing.netloc, listing.path) != ("https", "vergabeplattform.charite.de", "/index.php"):
        return None
    if row.tag != "tr" or not isinstance(extracted_id, str):
        return None
    cell = row.find("td")
    notice_ref = " ".join(cell.text_content().split()) if cell is not None else ""
    record_id = row.get("data-tendersid", "")
    if not re.fullmatch(r"[0-9]{1,20}", record_id) or extracted_id not in {notice_ref, record_id}:
        return None
    for button in row.xpath(".//*[@onclick]"):
        match = _ANSWER_BUTTON.fullmatch(button.get("onclick", "").strip())
        if match and match[1] == record_id:
            return _PUBLIC_ANSWERS + record_id
    return None
