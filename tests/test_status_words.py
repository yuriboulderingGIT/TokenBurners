"""Award, result and cancellation labels in European languages are not delivered as open tenders.

Erik's review (P2, plan v4: open): only a few exact words were recognised, so "awarded", "gegund",
"vergeben", "attribué" or a result notice became `open`. The track: a wrong value is worse than a
missing one.
"""

import pytest

from app.models import Locale, RawTender
from brain.normalize import normalize


def _status(label: str | None = None, deadline: str | None = None, locale: Locale = Locale()) -> tuple:
    fields = {"id": "T1", "title": "Roof"}
    if label is not None:
        fields["status"] = label
    if deadline is not None:
        fields["submissionDeadline"] = deadline
    tender = normalize(RawTender(url="https://portal.example/notice/1", fields=fields), locale)
    return tender.status, tender.submissionDeadline


@pytest.mark.parametrize("label", [
    "awarded", "Contract award notice", "CONTRACT_AWARD", "TenderResultNotice",  # English, feed constants
    "Aankondiging gegunde opdracht - algemene richtlijn, standaardregeling", "Voorlopige gunning",  # Dutch
    "Vergeben", "Zuschlag erteilt", "Abgeschlossen",  # German
    "Attribué", "Avis d'attribution", "Résultat de marché", "Clôturée", "Infructueux",  # French
    "Adjudicada", "Formalizada", "Resuelta", "Desierta",  # Spanish
    "Aggiudicata", "Esito di gara", "Scaduta",  # Italian
    "Rozstrzygnięty", "Ogłoszenie o wyniku postępowania", "Zakończony",  # Polish
    "Zadána", "Ukončeno",  # Czech
    "Tildelt", "Tilldelad",  # Norwegian/Danish, Swedish
])
def test_award_and_result_labels_are_closed(label):
    assert _status(label)[0] == "closed"


@pytest.mark.parametrize("label", [
    "Cancelled", "Terminated", "Vroegtijdige beëindiging", "Ingetrokken",  # English, Dutch
    "Aufgehoben", "Aufhebung des Verfahrens", "Annulé", "Déclaré sans suite",  # German, French
    "Anulada", "Revocata", "Odwołany", "Unieważniony", "Zrušeno",  # Spanish, Italian, Polish, Czech
    "Avlyst", "Avbruten", "Peruttu",  # Norwegian, Swedish, Finnish
])
def test_cancellation_labels_are_withdrawn(label):
    assert _status(label)[0] == "withdrawn"


@pytest.mark.parametrize("label", [
    None, "Open", "Active", "Gepubliceerd", "Aankondiging van een opdracht", "ContractNotice", "En cours",
    "Laufend", "Vergabeverfahren läuft", "Avis de marché", "Ogłoszenie o zamówieniu",
    "Neukončeno", "Zadávání", "Zadání veřejné zakázky",  # Czech: not finished / awarding / assignment
    "A" * 121 + " awarded",  # a description mapped as status is not trusted for its words
])
def test_labels_for_running_procedures_stay_open(label):
    assert _status(label)[0] == "open"


def test_a_past_date_only_deadline_closes_without_inventing_a_time():
    assert _status(deadline="2020-01-01", locale=Locale(country="DE")) == ("closed", None)
    assert _status(deadline="01.02.2099", locale=Locale(country="DE")) == ("open", None)
