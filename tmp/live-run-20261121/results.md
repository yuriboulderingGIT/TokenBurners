# Watchtower portal results

Observed 2026-11-20T23:16:34+00:00. Counts include all held tenders and their documents. Cost is cumulative recorded model spend per portal in this data folder.

| Portal | State | Reason | Tenders | Documents | Model USD |
|---|---|---|---:|---:|---:|
| etenders-ie | failed | could not attach: pagination exceeded the recipe's max_pages; max_pages must be between 1 and 100 | 0 | 0 | 0.051644 |
| sell2wales-gb | failed | could not attach: the next page control provides no public HTTP link and no usable public tender feed was found | 0 | 0 | 0.002139 |
| vergabemarktplatz-nrw-de | serving | delivering 44 tenders. Latest read verified 0 documents | 44 | 0 | 0.007138 |
| dtvp-de | failed | could not attach: the next listing page repeated the same notice rows | 0 | 0 | 0.064341 |
| boamp-fr | failed | could not attach: the JSON list advertised 1728938 tenders, but pagination exposed only 3 entries | 0 | 0 | 0.000898 |
| tenderned-nl | failed | could not attach: the listing requires JavaScript and no usable public tender feed was found | 0 | 0 | 0.000000 |
| place-fr | failed | could not attach: the next page control provides no public HTTP link and no usable public tender feed was found | 0 | 0 | 0.001556 |
| etenders-cy | serving | delivering 10 tenders. Latest read verified 4 documents | 10 | 4 | 0.059175 |
| etenders-mt | failed | could not attach: pagination exceeded the recipe's max_pages; invalid XPath for id; max_pages must be between 1 and 100 | 0 | 0 | 0.044782 |
| vergabe-brandenburg-de | serving | delivering 44 tenders. Latest read verified 0 documents | 44 | 0 | 0.028651 |

## Model usage

Completed runs in this data folder, grouped by outcome. Runs with unresolved usage reservations are excluded. Amounts come from the model call ledger.

| Operation | Result | Runs | Model calls | Total USD | USD per run |
|---|---|---:|---:|---:|---:|
| attach | failed | 7 | 15 | 0.165360 | 0.023623 |
| attach | ok | 3 | 11 | 0.094964 | 0.031655 |
