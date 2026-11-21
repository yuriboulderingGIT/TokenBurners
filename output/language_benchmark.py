"""Offline language coverage benchmark for deterministic Watchtower readers.

Run from the repository root with the project Python. The report includes synthetic,
labelled cases and a small smoke set from checked-in public portal fixtures. It makes
no network calls and no model calls. Synthetic successes measure only listed rules;
they are not a real-world accuracy estimate.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from lxml import html

from app.models import Locale, RawTender
from brain.js_discover import _feed_rank
from brain.normalize import normalize
from brain.notice_evidence import _kind
from brain.notice_fields import listed_deadline_column, notice_facts
from brain.run_recipe import next_page_controls


ROOT = Path(__file__).resolve().parents[1]
BASE = "https://language-benchmark.example"
results: list[tuple[str, str, str, bool, str]] = []


def record(language: str, component: str, name: str, passed: bool, detail: str = "") -> None:
    results.append((language, component, name, passed, detail))


FIELD_CASES = [
    ("de", "Auftraggeber", "buyerName"), ("de", "Angebotsfrist", "submissionDeadline"),
    ("fr", "Acheteur", "buyerName"), ("fr", "Date limite de réception des offres", "submissionDeadline"),
    ("fr", "Numéro de référence", "referenceNumber"),
    ("es", "Órgano de contratación", "buyerName"),
    ("es", "Fecha límite de presentación", "submissionDeadline"),
    ("it", "Committente", "buyerName"),
    ("it", "Termine per la presentazione delle offerte", "submissionDeadline"),
    ("nl", "Aanbestedende dienst", "buyerName"), ("nl", "Uiterste datum indiening", "submissionDeadline"),
    ("pl", "Zamawiający", "buyerName"), ("pl", "Termin składania ofert", "submissionDeadline"),
    ("cs", "Zadavatel", "buyerName"), ("cs", "Lhůta pro podání nabídek", "submissionDeadline"),
    ("sk", "Verejný obstarávateľ", "buyerName"),
    ("sk", "Lehota na predkladanie ponúk", "submissionDeadline"),
    ("el", "Αναθέτουσα Αρχή", "buyerName"), ("el", "Προθεσμία υποβολής προσφορών", "submissionDeadline"),
    ("hu", "Ajánlatkérő", "buyerName"), ("hu", "Ajánlattételi határidő", "submissionDeadline"),
]

for language, label, expected in FIELD_CASES:
    page = html.fromstring(f"<dl><dt>{label}</dt><dd>fixture-value</dd></dl>")
    actual = notice_facts(page).get(expected)
    record(language, "field labels", label, actual is not None, f"expected {expected}")


NEXT_CASES = [
    ("en", "Next page"), ("de", "Nächste Seite"), ("fr", "Page suivante"),
    ("es", "Página siguiente"), ("it", "Pagina successiva"), ("nl", "Volgende pagina"),
    ("pl", "Następna strona"), ("cs", "Další stránka"), ("sk", "Ďalšia strana"),
    ("el", "Επόμενη σελίδα"), ("hu", "Következő oldal"), ("ro", "Pagina următoare"),
    ("fi", "Seuraava sivu"), ("sv", "Nästa sida"), ("bg", "Следваща страница"),
]

for language, label in NEXT_CASES:
    page = html.fromstring(f'<nav><a href="?page=2">{label}</a></nav>')
    found = bool(next_page_controls(page, BASE + "/list"))
    record(language, "pagination", label, found, "expected next-page control")


KIND_CASES = [
    ("de", "Ausschreibungen", "notice"), ("fr", "Avis de marché", "notice"),
    ("es", "Licitaciones", "notice"), ("it", "Bandi di gara", "notice"),
    ("nl", "Aanbestedingen", "notice"), ("pl", "Przetargi", "notice"),
    ("cs", "Veřejné zakázky", "notice"), ("el", "Διαγωνισμός", "notice"),
    ("de", "CPV Kategorien", "directory, news or navigation"),
    ("fr", "Catégories CPV", "directory, news or navigation"),
    ("el", "Κατηγορίες CPV", "directory, news or navigation"),
]

for language, label, expected in KIND_CASES:
    actual = _kind(label)
    record(language, "record type", label, actual == expected, f"expected {expected}; got {actual}")


FEED_CASES = [
    ("de", "/api/ausschreibungen", {"id": "1", "titel": "Bauarbeiten"}),
    ("fr", "/api/avis", {"id": "1", "objet": "Travaux"}),
    ("es", "/api/licitaciones", {"id": "1", "objeto": "Obras"}),
    ("it", "/api/bandi", {"id": "1", "oggetto": "Lavori"}),
    ("nl", "/api/aanbestedingen", {"id": "1", "naam": "Werk"}),
    ("pl", "/api/przetargi", {"id": "1", "nazwa": "Roboty"}),
    ("cs", "/api/zakázky", {"id": "1", "název": "Stavba"}),
    ("el", "/api/διαγωνισμοι", {"id": "1", "τίτλος": "Έργα"}),
    ("fi", "/api/hankinnat", {"id": "1", "otsikko": "Työ"}),
]

for language, endpoint, item in FEED_CASES:
    score = _feed_rank([item], endpoint)
    record(language, "API feed ranking", endpoint, score >= 4, f"score={score}; expected >=4")


DATE_CASES = [
    ("de", "10.12.2026, 23:59 Uhr", Locale(lang="de", country="DE", tz="Europe/Berlin"),
     "2026-12-10T23:59:00+01:00"),
    ("fr", "9 décembre 2026, 10h30", Locale(lang="fr", country="FR", tz="Europe/Paris"),
     "2026-12-09T10:30:00+01:00"),
    ("es", "9 de diciembre de 2026, 10:00", Locale(lang="es", country="ES", tz="Europe/Madrid"),
     "2026-12-09T10:00:00+01:00"),
    ("it", "9 dicembre 2026, 10:00", Locale(lang="it", country="IT", tz="Europe/Rome"),
     "2026-12-09T10:00:00+01:00"),
    ("nl", "9 december 2026, 10:00", Locale(lang="nl", country="NL", tz="Europe/Amsterdam"),
     "2026-12-09T10:00:00+01:00"),
    ("pl", "9 grudnia 2026, 10:00", Locale(lang="pl", country="PL", tz="Europe/Warsaw"),
     "2026-12-09T10:00:00+01:00"),
    ("cs", "09.12.2026, 10:00", Locale(lang="cs", country="CZ", tz="Europe/Prague"),
     "2026-12-09T10:00:00+01:00"),
    ("el", "Fri Nov 20 15:29:24 EET 2026", Locale(lang="el", country="CY", tz="Asia/Nicosia"),
     "2026-11-20T15:29:24+02:00"),
]

for language, value, locale, expected in DATE_CASES:
    tender = normalize(RawTender(url=BASE + "/notice/1", fields={"id": "1", "submissionDeadline": value}), locale)
    actual = tender.submissionDeadline if tender else None
    record(language, "dates", value, actual == expected, f"expected {expected}; got {actual}")


STATUS_CASES = [
    ("de", "Zuschlag erteilt", Locale(lang="de"), "closed"),
    ("fr", "Annulé", Locale(lang="fr"), "withdrawn"),
    ("es", "Adjudicada", Locale(lang="es"), "closed"),
    ("it", "Revocata", Locale(lang="it"), "withdrawn"),
    ("nl", "Ingetrokken", Locale(lang="nl"), "withdrawn"),
    ("pl", "Unieważniony", Locale(lang="pl"), "withdrawn"),
    ("cs", "Zrušeno", Locale(lang="cs"), "withdrawn"),
]

for language, value, locale, expected in STATUS_CASES:
    tender = normalize(RawTender(url=BASE + "/notice/1", fields={"id": "1", "status": value}), locale)
    actual = tender.status if tender else None
    record(language, "status meaning", value, actual == expected, f"expected {expected}; got {actual}")


def fixture_check(language: str, name: str, condition: bool, detail: str) -> None:
    record(language, "real fixture smoke", name, condition, detail)


# Greek ePPS: its Greek headers and values coexist with English server timestamps.
cyprus = html.parse(str(ROOT / "testset/fixtures/etenders-cy/list.html"))
cy_row = cyprus.xpath("//table[@id='T01']/tbody/tr")[0]
cy_tender = normalize(RawTender(url=BASE + "/notice/cy", fields={
    "id": cy_row.xpath("string(td[3])"), "title": cy_row.xpath("string(td[2]//a)"),
    "buyerName": cy_row.xpath("string(td[4])"), "publicationDate": cy_row.xpath("string(td[6])"),
    "submissionDeadline": cy_row.xpath("string(td[7])"), "status": cy_row.xpath("string(td[8])"),
}), Locale(lang="el", country="CY"))
fixture_check("el", "etenders-cy first notice", bool(cy_tender and cy_tender.publicationDate == "2026-11-20"
              and cy_tender.submissionDeadline == "2026-12-21T11:00:00+02:00"
              and cy_tender.buyerName), "Greek fields, English server date, Cyprus time zone")
fixture_check("el", "Greek deadline column label", listed_deadline_column(cy_row) == 6,
              "Προθεσμία υποβολής προσφορών should map to column 6")

# French BOAMP JSON: source-native property names and ISO dates.
boamp = json.loads((ROOT / "testset/fixtures/boamp-fr/list.json").read_text(encoding="utf-8"))["results"][0]
fr_tender = normalize(RawTender(url=boamp["url_avis"], fields={
    "id": boamp["idweb"], "title": boamp["objet"], "buyerName": boamp["nomacheteur"],
    "publicationDate": boamp["dateparution"], "submissionDeadline": boamp["datelimitereponse"],
}), Locale(lang="fr", country="FR"))
fixture_check("fr", "BOAMP first notice", bool(fr_tender and fr_tender.id == "26-112058"
              and fr_tender.publicationDate == "2026-11-20" and fr_tender.buyerName),
              "French JSON field names and ISO publication date")

# German NRW table: a localized heading independently identifies the deadline column.
nrw = html.parse(str(ROOT / "testset/fixtures/vergabemarktplatz-nrw-de/list.html"))
nrw_row = nrw.xpath("//tbody/tr")[0]
from brain.notice_fields import listed_deadline_column

fixture_check("de", "NRW localized deadline header", listed_deadline_column(nrw_row) == 1,
              "Angebots- / Teilnahmefrist column should be detected")


def markdown_report() -> str:
    groups: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for language, component, _name, passed, _detail in results:
        groups[(language, component)].append(passed)
    lines = [
        "# Watchtower language benchmark",
        "",
        "Offline baseline generated from the current checkout. Synthetic labeled cases probe known deterministic rules; fixture cases use checked-in portal evidence. This is not a production accuracy estimate and does not measure model selection or live-site attachment success.",
        "",
        f"Total: **{sum(passed for *_, passed, _detail in results)}/{len(results)} checks passed** across {len({row[0] for row in results})} languages.",
        "",
        "| Language | Component | Passed | Coverage |",
        "|---|---|---:|---:|",
    ]
    for (language, component), checks in sorted(groups.items()):
        lines.append(f"| `{language}` | {component} | {sum(checks)}/{len(checks)} | {100 * sum(checks) / len(checks):.0f}% |")
    failed = [row for row in results if not row[3]]
    lines.extend(["", "## Failed cases", ""])
    if failed:
        lines.extend(f"- `{language}` / {component}: {name} — {detail}" for language, component, name, _, detail in failed)
    else:
        lines.append("No failures in the current case list.")
    lines.extend(["", "## Case-level results", "", "| Language | Component | Case | Result | Detail |", "|---|---|---|---|---|"])
    for language, component, name, passed, detail in results:
        lines.append(f"| `{language}` | {component} | {name.replace('|', '/')} | {'pass' if passed else 'FAIL'} | {detail.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    destination = ROOT / "output" / "language-benchmark-2026-11-21.md"
    destination.write_text(markdown_report(), encoding="utf-8")
    print(destination)
    print(markdown_report().splitlines()[4])
