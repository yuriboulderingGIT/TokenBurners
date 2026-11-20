# B14 real-portal run

On 20 November 2026, 18:23–18:38 CET, Watchtower attempted all ten public portals. All ten finished in `failed`. **No portal delivered a verified complete listing or a document.** The model ledger recorded 46 billed calls totaling **$0.66145** across the initial attempts, diagnosis and retries. No budget reservations were outstanding at the end.

| Portal | Final state | Reason observed on the final run | Stored tenders | Documents | Model cost (USD) |
|---|---|---|---:|---:|---:|
| etenders-ie | failed | The proposed page limit was outside the allowed range of 1–100. | 0 | 0 | 0.11039 |
| dtvp-de | failed | The listing continued beyond the recipe’s page limit. | 0 | 0 | 0.11554 |
| sell2wales-gb | failed | The Next button submits a form. Page 1 of 382 had no direct HTTP link to the next page. | 0 | 0 | 0.01700 |
| boamp-fr | failed | The stored HTML recipe encountered JSON. A fresh JSON attach also failed to validate a usable feed. | 1* | 0 | 0.00748 |
| tenderned-nl | failed | The list selector returned no tenders. Browser discovery did not yield a working JSON recipe. | 0 | 0 | 0.07374 |
| bekanntmachungsservice-de | failed | The list selector returned no tenders. Browser discovery did not yield a working JSON recipe. | 0 | 0 | 0.06511 |
| bosa-eprocurement-be | failed | The list selector returned no tenders. Browser discovery did not yield a working JSON recipe. | 0 | 0 | 0.06435 |
| vergabemarktplatz-nrw-de | failed | A portal redirect left the recipe’s permitted origin. | 0 | 0 | 0.09604 |
| place-fr | failed | The sampled tenders did not supply both a portal-issued ID and a title. | 0 | 0 | 0.11179 |
| public-contracts-scotland-gb | failed | The default HTTP client could not verify the issuer of the site’s TLS certificate. A separate fetch using Windows’ native certificate-chain validation returned HTTP 200. | 0 | 0 | 0.00000 |

\* BOAMP’s first recipe read the JSON response as HTML and stored one row. Watchtower retained that row after the corrected reader rejected the recipe. It is excluded from verified deliveries.

## What changed during the run

- **Model requests.** Eight first attempts received HTTP 400 because the input message omitted the explicit JSON instruction required by the provider. Adding it let those requests proceed. Six portals subsequently hit exactly 4,096 output tokens. Retries used low reasoning effort and a 16,384-token allowance, with the larger maximum included in each budget reservation.
- **Listing completeness.** The reader now follows a public next-page URL on the Irish portal’s button and detects Sell2Wales’ form-only pager. Direct JSON entries go through JSON recipe validation. These checks exposed partial listings before they could be counted as successful reads.
- **JavaScript discovery.** The German service loaded a 3.1 MB script and TenderNed exhausted the original 16-request allowance while loading scripts. The browser now permits scripts up to 4 MiB and up to 64 rate-limited requests. All three JavaScript portals still lacked a validated tender recipe on retry.

The final failures comprised four collection or pagination problems, three JavaScript listings, one cross-origin redirect, one incomplete tender sample and one TLS verification failure.

## Run conditions

The run used the edited Windows checkout based on `09f1abd`. A temporary driver posted the entries from `testset/portals.yaml` through FastAPI and ran the normal worker against the real portals and models. P17’s `testset/run_all.py` and Docker were unavailable locally. The run used the project’s SQLite database, model cache, rate limiter and configured $5 budget. Costs come from `llm_calls.cost_usd` grouped by portal and include unsuccessful calls with reported usage.
