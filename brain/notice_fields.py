"""Read explicitly labelled facts on an individual procurement notice."""

import re
import unicodedata
from urllib.parse import unquote

from lxml import html


_LABELS = {
    "id": ("identifier", "tender id", "notice id", "kennung", "vergabenummer", "numéro d'avis",
          "identificador de licitación", "id zakázky", "azonosító"),
    "buyerName": ("buyer", "contracting authority", "auftraggeber", "öffentlicher auftraggeber", "vergabestelle",
                  "acheteur", "pouvoir adjudicateur", "comprador", "órgano de contratación", "committente",
                  "aanbesteder", "aanbestedende dienst", "zamawiający", "zadavatel", "veřejný zadavatel",
                  "verejný obstarávateľ", "αναθέτουσα αρχή", "ajánlatkérő"),
    "submissionDeadline": ("submission deadline", "closing date", "angebotsfrist", "schlusstermin",
                           "angebots- / teilnahmefrist",
                           "date limite de réception des offres", "date limite de remise des offres",
                           "fecha límite de presentación", "termine per la presentazione delle offerte",
                           "uiterste datum indiening", "termin składania ofert", "lhůta pro podání nabídek",
                           "lehota na predkladanie ponúk", "προθεσμία υποβολής προσφορών", "ajánlattételi határidő"),
    "publicationDate": ("publication", "published on", "publication date", "veröffentlicht am",
                        "date de publication", "fecha de publicación", "data di pubblicazione", "publicatiedatum",
                        "data publikacji", "datum uveřejnění", "dátum uverejnenia", "ημερομηνία δημοσίευσης"),
    "referenceNumber": ("reference number", "ref.", "aktenzeichen", "az.", "numéro de référence",
                        "numero di referencia", "numero di riferimento", "referentienummer", "numer referencyjny",
                        "evidenční číslo", "iktatószám"),
    "procedureType": ("procedure type", "verfahrensart", "type de procédure", "tipo de procedimiento",
                      "tipo di procedura", "proceduretype", "rodzaj procedury", "druh zadávacího řízení"),
    "contractNature": ("contract nature", "art des auftrags", "nature du marché", "naturaleza del contrato",
                       "natura dell'appalto", "aard van de opdracht", "rodzaj zamówienia", "druh zakázky",
                       "αντικείμενο σύμβασης"),
    "cpvCodes": ("cpv code", "cpv codes", "cpv", "code cpv", "código cpv", "codice cpv", "codul cpv", "kod cpv"),
    "status": ("status", "verfahrensstatus", "tender status", "statut", "estado", "stato", "status zamówienia",
               "stav zakázky", "állapot", "κατάσταση"),
    "placeOfPerformance": ("place of performance", "erfüllungsort", "lieu d'exécution", "lugar de ejecución",
                           "luogo di esecuzione", "plaats van uitvoering", "miejsce realizacji", "místo plnění",
                           "miesto plnenia", "τόπος εκτέλεσης"),
}


def _label_key(label: str) -> str:
    """Compare labels across accents, punctuation, and whitespace without changing source values."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", label.casefold())
                     if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", folded).split())


_KEY = {_label_key(label): key for key, labels in _LABELS.items() for label in labels}


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def listed_deadline_column(row: html.HtmlElement) -> int | None:
    """Use an unambiguous header on a simple results table as independent deadline evidence."""
    table = next(row.iterancestors("table"), None)
    if table is None:
        return None
    headers = table.xpath("./thead/tr[th] | ./tr[th] | ./tbody/tr[th]")
    if len(headers) != 1 or headers[0].xpath("./th[@colspan or @rowspan]"):
        return None
    indices = [n for n, th in enumerate(headers[0].xpath("./th"))
               if _KEY.get(_label_key(th.text_content().rstrip(":"))) == "submissionDeadline"
               or _label_key(th.text_content()) == "deadline"]
    return indices[0] if len(indices) == 1 else None


def notice_facts(page: html.HtmlElement) -> dict[str, tuple[str, str]]:
    """Return unambiguous (source value, reusable selector) pairs."""
    facts = {}
    ambiguous = set()
    for node in page.xpath("//tr[count(th)=1 and count(td)=1]/th | //dl/dt[following-sibling::*[1][self::dd]]"):
        label = " ".join(node.text_content().split())
        key = _KEY.get(_label_key(label.rstrip(":")))
        if not key:
            continue
        sibling = "td" if node.tag == "th" else "dd"
        target = node.getnext()
        if target is None or target.tag != sibling:
            continue
        value = " ".join(target.text_content().split())
        if not value:
            continue
        # Recheck the same localized label on every notice.
        selector = (f"//{node.tag}[normalize-space(.)={_xpath_literal(label)}]"
                    f"/following-sibling::{sibling}[1]")
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
