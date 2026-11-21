# Watchtower language benchmark

Offline baseline generated from the current checkout. Synthetic labeled cases probe known deterministic rules; fixture cases use checked-in portal evidence. This is not a production accuracy estimate and does not measure model selection or live-site attachment success.

Total: **75/75 checks passed** across 15 languages.

| Language | Component | Passed | Coverage |
|---|---|---:|---:|
| `bg` | pagination | 1/1 | 100% |
| `cs` | API feed ranking | 1/1 | 100% |
| `cs` | dates | 1/1 | 100% |
| `cs` | field labels | 2/2 | 100% |
| `cs` | pagination | 1/1 | 100% |
| `cs` | record type | 1/1 | 100% |
| `cs` | status meaning | 1/1 | 100% |
| `de` | API feed ranking | 1/1 | 100% |
| `de` | dates | 1/1 | 100% |
| `de` | field labels | 2/2 | 100% |
| `de` | pagination | 1/1 | 100% |
| `de` | real fixture smoke | 1/1 | 100% |
| `de` | record type | 2/2 | 100% |
| `de` | status meaning | 1/1 | 100% |
| `el` | API feed ranking | 1/1 | 100% |
| `el` | dates | 1/1 | 100% |
| `el` | field labels | 2/2 | 100% |
| `el` | pagination | 1/1 | 100% |
| `el` | real fixture smoke | 2/2 | 100% |
| `el` | record type | 2/2 | 100% |
| `en` | pagination | 1/1 | 100% |
| `es` | API feed ranking | 1/1 | 100% |
| `es` | dates | 1/1 | 100% |
| `es` | field labels | 2/2 | 100% |
| `es` | pagination | 1/1 | 100% |
| `es` | record type | 1/1 | 100% |
| `es` | status meaning | 1/1 | 100% |
| `fi` | API feed ranking | 1/1 | 100% |
| `fi` | pagination | 1/1 | 100% |
| `fr` | API feed ranking | 1/1 | 100% |
| `fr` | dates | 1/1 | 100% |
| `fr` | field labels | 3/3 | 100% |
| `fr` | pagination | 1/1 | 100% |
| `fr` | real fixture smoke | 1/1 | 100% |
| `fr` | record type | 2/2 | 100% |
| `fr` | status meaning | 1/1 | 100% |
| `hu` | field labels | 2/2 | 100% |
| `hu` | pagination | 1/1 | 100% |
| `it` | API feed ranking | 1/1 | 100% |
| `it` | dates | 1/1 | 100% |
| `it` | field labels | 2/2 | 100% |
| `it` | pagination | 1/1 | 100% |
| `it` | record type | 1/1 | 100% |
| `it` | status meaning | 1/1 | 100% |
| `nl` | API feed ranking | 1/1 | 100% |
| `nl` | dates | 1/1 | 100% |
| `nl` | field labels | 2/2 | 100% |
| `nl` | pagination | 1/1 | 100% |
| `nl` | record type | 1/1 | 100% |
| `nl` | status meaning | 1/1 | 100% |
| `pl` | API feed ranking | 1/1 | 100% |
| `pl` | dates | 1/1 | 100% |
| `pl` | field labels | 2/2 | 100% |
| `pl` | pagination | 1/1 | 100% |
| `pl` | record type | 1/1 | 100% |
| `pl` | status meaning | 1/1 | 100% |
| `ro` | pagination | 1/1 | 100% |
| `sk` | field labels | 2/2 | 100% |
| `sk` | pagination | 1/1 | 100% |
| `sv` | pagination | 1/1 | 100% |

## Failed cases

No failures in the current case list.

## Case-level results

| Language | Component | Case | Result | Detail |
|---|---|---|---|---|
| `de` | field labels | Auftraggeber | pass | expected buyerName |
| `de` | field labels | Angebotsfrist | pass | expected submissionDeadline |
| `fr` | field labels | Acheteur | pass | expected buyerName |
| `fr` | field labels | Date limite de réception des offres | pass | expected submissionDeadline |
| `fr` | field labels | Numéro de référence | pass | expected referenceNumber |
| `es` | field labels | Órgano de contratación | pass | expected buyerName |
| `es` | field labels | Fecha límite de presentación | pass | expected submissionDeadline |
| `it` | field labels | Committente | pass | expected buyerName |
| `it` | field labels | Termine per la presentazione delle offerte | pass | expected submissionDeadline |
| `nl` | field labels | Aanbestedende dienst | pass | expected buyerName |
| `nl` | field labels | Uiterste datum indiening | pass | expected submissionDeadline |
| `pl` | field labels | Zamawiający | pass | expected buyerName |
| `pl` | field labels | Termin składania ofert | pass | expected submissionDeadline |
| `cs` | field labels | Zadavatel | pass | expected buyerName |
| `cs` | field labels | Lhůta pro podání nabídek | pass | expected submissionDeadline |
| `sk` | field labels | Verejný obstarávateľ | pass | expected buyerName |
| `sk` | field labels | Lehota na predkladanie ponúk | pass | expected submissionDeadline |
| `el` | field labels | Αναθέτουσα Αρχή | pass | expected buyerName |
| `el` | field labels | Προθεσμία υποβολής προσφορών | pass | expected submissionDeadline |
| `hu` | field labels | Ajánlatkérő | pass | expected buyerName |
| `hu` | field labels | Ajánlattételi határidő | pass | expected submissionDeadline |
| `en` | pagination | Next page | pass | expected next-page control |
| `de` | pagination | Nächste Seite | pass | expected next-page control |
| `fr` | pagination | Page suivante | pass | expected next-page control |
| `es` | pagination | Página siguiente | pass | expected next-page control |
| `it` | pagination | Pagina successiva | pass | expected next-page control |
| `nl` | pagination | Volgende pagina | pass | expected next-page control |
| `pl` | pagination | Następna strona | pass | expected next-page control |
| `cs` | pagination | Další stránka | pass | expected next-page control |
| `sk` | pagination | Ďalšia strana | pass | expected next-page control |
| `el` | pagination | Επόμενη σελίδα | pass | expected next-page control |
| `hu` | pagination | Következő oldal | pass | expected next-page control |
| `ro` | pagination | Pagina următoare | pass | expected next-page control |
| `fi` | pagination | Seuraava sivu | pass | expected next-page control |
| `sv` | pagination | Nästa sida | pass | expected next-page control |
| `bg` | pagination | Следваща страница | pass | expected next-page control |
| `de` | record type | Ausschreibungen | pass | expected notice; got notice |
| `fr` | record type | Avis de marché | pass | expected notice; got notice |
| `es` | record type | Licitaciones | pass | expected notice; got notice |
| `it` | record type | Bandi di gara | pass | expected notice; got notice |
| `nl` | record type | Aanbestedingen | pass | expected notice; got notice |
| `pl` | record type | Przetargi | pass | expected notice; got notice |
| `cs` | record type | Veřejné zakázky | pass | expected notice; got notice |
| `el` | record type | Διαγωνισμός | pass | expected notice; got notice |
| `de` | record type | CPV Kategorien | pass | expected directory, news or navigation; got directory, news or navigation |
| `fr` | record type | Catégories CPV | pass | expected directory, news or navigation; got directory, news or navigation |
| `el` | record type | Κατηγορίες CPV | pass | expected directory, news or navigation; got directory, news or navigation |
| `de` | API feed ranking | /api/ausschreibungen | pass | score=4; expected >=4 |
| `fr` | API feed ranking | /api/avis | pass | score=4; expected >=4 |
| `es` | API feed ranking | /api/licitaciones | pass | score=4; expected >=4 |
| `it` | API feed ranking | /api/bandi | pass | score=4; expected >=4 |
| `nl` | API feed ranking | /api/aanbestedingen | pass | score=4; expected >=4 |
| `pl` | API feed ranking | /api/przetargi | pass | score=4; expected >=4 |
| `cs` | API feed ranking | /api/zakázky | pass | score=4; expected >=4 |
| `el` | API feed ranking | /api/διαγωνισμοι | pass | score=4; expected >=4 |
| `fi` | API feed ranking | /api/hankinnat | pass | score=4; expected >=4 |
| `de` | dates | 10.12.2026, 23:59 Uhr | pass | expected 2026-12-10T23:59:00+01:00; got 2026-12-10T23:59:00+01:00 |
| `fr` | dates | 9 décembre 2026, 10h30 | pass | expected 2026-12-09T10:30:00+01:00; got 2026-12-09T10:30:00+01:00 |
| `es` | dates | 9 de diciembre de 2026, 10:00 | pass | expected 2026-12-09T10:00:00+01:00; got 2026-12-09T10:00:00+01:00 |
| `it` | dates | 9 dicembre 2026, 10:00 | pass | expected 2026-12-09T10:00:00+01:00; got 2026-12-09T10:00:00+01:00 |
| `nl` | dates | 9 december 2026, 10:00 | pass | expected 2026-12-09T10:00:00+01:00; got 2026-12-09T10:00:00+01:00 |
| `pl` | dates | 9 grudnia 2026, 10:00 | pass | expected 2026-12-09T10:00:00+01:00; got 2026-12-09T10:00:00+01:00 |
| `cs` | dates | 09.12.2026, 10:00 | pass | expected 2026-12-09T10:00:00+01:00; got 2026-12-09T10:00:00+01:00 |
| `el` | dates | Fri Nov 20 15:29:24 EET 2026 | pass | expected 2026-11-20T15:29:24+02:00; got 2026-11-20T15:29:24+02:00 |
| `de` | status meaning | Zuschlag erteilt | pass | expected closed; got closed |
| `fr` | status meaning | Annulé | pass | expected withdrawn; got withdrawn |
| `es` | status meaning | Adjudicada | pass | expected closed; got closed |
| `it` | status meaning | Revocata | pass | expected withdrawn; got withdrawn |
| `nl` | status meaning | Ingetrokken | pass | expected withdrawn; got withdrawn |
| `pl` | status meaning | Unieważniony | pass | expected withdrawn; got withdrawn |
| `cs` | status meaning | Zrušeno | pass | expected withdrawn; got withdrawn |
| `el` | real fixture smoke | etenders-cy first notice | pass | Greek fields, English server date, Cyprus time zone |
| `el` | real fixture smoke | Greek deadline column label | pass | Προθεσμία υποβολής προσφορών should map to column 6 |
| `fr` | real fixture smoke | BOAMP first notice | pass | French JSON field names and ISO publication date |
| `de` | real fixture smoke | NRW localized deadline header | pass | Angebots- / Teilnahmefrist column should be detected |
