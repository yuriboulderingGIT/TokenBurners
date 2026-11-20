# Watchtower portal results

Observed 2026-11-20T17:49:53+00:00. Counts include all held tenders and their documents. Cost is cumulative recorded model spend per portal in this data folder.

| Portal | State | Reason | Tenders | Documents | Model USD |
|---|---|---|---:|---:|---:|
| etenders-ie | failed | could not attach: Expecting value: line 1 column 1 (char 0) | 0 | 0 | 0.076754 |
| sell2wales-gb | failed | could not attach: | 0 | 0 | 0.064718 |
| vergabemarktplatz-nrw-de | failed | could not attach: Expecting value: line 1 column 1 (char 0) | 0 | 0 | 0.065485 |
| dtvp-de | failed | could not attach: Expecting value: line 1 column 1 (char 0) | 0 | 0 | 0.132259 |
| boamp-fr | failed | could not attach: the JSON list advertised 1728932 tenders, but pagination exposed only 3 entries | 0 | 0 | 0.057946 |
| tenderned-nl | failed | could not attach: sample tenders need a portal-issued ID and a title | 0 | 0 | 0.036098 |
| place-fr | failed | could not attach: sample tenders need a portal-issued ID and a title | 0 | 0 | 0.062210 |
| etenders-cy | serving | delivering 10 tenders | 10 | 4 | 0.019190 |
| etenders-mt | failed | could not attach: list.next_page selected a URL absent from the page's links | 0 | 0 | 0.065765 |
| vergabe-brandenburg-de | failed | could not attach: Expecting value: line 1 column 1 (char 0) | 0 | 0 | 0.065747 |
| mock | serving | delivering 5 tenders | 5 | 8 | 0.033804 |

## Model usage

Completed runs in this data folder, grouped by outcome. Runs with unresolved usage reservations are excluded. Amounts come from the model call ledger.

| Operation | Result | Runs | Model calls | Total USD | USD per run |
|---|---|---:|---:|---:|---:|
| attach | failed | 20 | 38 | 0.626982 | 0.031349 |
| attach | ok | 2 | 5 | 0.024675 | 0.012338 |
| read | failed | 1 | 0 | 0.000000 | 0.000000 |
| read | ok | 3 | 0 | 0.000000 | 0.000000 |
| repair | ok | 1 | 1 | 0.028319 | 0.028319 |

## Delivery checks

- A fresh layout broke the mock recipe. The real model repaired it in one call, preserving all five portal IDs and eight files. A regular refresh made zero model calls.
- A separate live read of Cyprus with model calls disabled returned ten notices, all ten publication dates and offset-bearing deadlines, and four files. All ten tender URLs pointed to the same listing. The source fixture contains individual notice links, so detail-link extraction remains incomplete.
- Four portals ended with a JSON parsing error. Each recorded a model response at the 4,096-token output cap. The token counts point to possible truncation. The ledger records no response text to confirm it.
- The Welsh portal documents a login requirement for hosted attachments. This run failed during attach before it could test a document. Its empty failure detail is preserved in the table. Subsequent builds supply a fallback explanation for that case.
