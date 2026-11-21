# Watchtower live-site check — 21 November 2026

Fresh local run against the ten public URLs in `testset/portals.yaml`. Watchtower used a separate database, the configured OpenAI key, model cache disabled, and the configured $5 cumulative cap. The runner observed final states at 2026-11-21 00:16 Bratislava time (2026-11-20 23:16 UTC). No portal timed out.

**Actual result: 1 of 10 worked, 9 did not deliver verified tenders.** Watchtower itself reported 3 `serving` and 7 `failed`, but two `serving` portals are false positives: all 44 rows for each are procurement category links, not tenders. Those category rows have no buyer, publication date, deadline, or documents. They should not count as successes.

| Portal | App state | Audit judgment | Tenders | Documents | Reason or evidence | Model USD |
|---|---|---|---:|---:|---|---:|
| etenders-ie | failed | Failed | 0 | 0 | Pagination exceeded recipe page limit; model then proposed a limit outside 1–100 | 0.051644 |
| sell2wales-gb | failed | Failed | 0 | 0 | Next-page control has no public HTTP link; no usable feed discovered | 0.002139 |
| vergabemarktplatz-nrw-de | serving | **False positive** | 44 category rows | 0 | All 44 URLs are `categoryOverview.do` CPV category links; 0 buyers and 0 deadlines | 0.007138 |
| dtvp-de | failed | Failed | 0 | 0 | Next listing page repeated the same rows | 0.064341 |
| boamp-fr | failed | Failed | 0 | 0 | API advertises 1,728,938 results; configured URL exposes only 3 | 0.000898 |
| tenderned-nl | failed | Failed | 0 | 0 | JavaScript listing; no usable public feed discovered | 0.000000 |
| place-fr | failed | Failed | 0 | 0 | Next-page control has no public HTTP link; no usable feed discovered | 0.001556 |
| etenders-cy | serving | **Working** | 10 | 4 | 10 distinct notice IDs and titles, 10 buyers, publication dates, and deadlines; 4 stored PDFs | 0.059175 |
| etenders-mt | failed | Failed | 0 | 0 | Pagination exceeded page limit; later candidate had invalid ID XPath/limit | 0.044782 |
| vergabe-brandenburg-de | serving | **False positive** | 44 category rows | 0 | All 44 URLs are `categoryOverview.do` CPV category links; 0 buyers and 0 deadlines | 0.028651 |

The four Cyprus document files exist in the isolated data folder and start with a PDF signature. The total recorded model cost was **$0.260324**, with **zero outstanding model reservations**. This is one run at this time; public sites can change.

The immediate quality bug is that the health check accepts category pages as tenders when they have unique IDs and titles. Fixing that should turn the NRW and Brandenburg false successes into honest failures until the reader reaches actual notices. The other major blockers are pagination and JavaScript feed discovery. See [site-reading-audit.md](site-reading-audit.md) for earlier reproduced reader defects.

The raw runner output and SQLite database are in `tmp/live-run-20261121/`. The database also contains the downloaded Cyprus PDFs under its `data/files/` directory.
