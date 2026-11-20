# B2 follow-up work

The 20 November B14 attempt finished with all ten portals in `failed` and no verified portal data. See [results.md](results.md) for per-portal state, reason and measured cost. Re-run this checklist after each fix with the real-model runner.

## Fix before accepting a portal as serving

- [ ] **etenders-ie:** make the generated pagination recipe use no more than the supported 100 pages. Validate all advertised pages and all sample tender IDs before attaching.
- [ ] **dtvp-de:** investigate why the listing advertises or returns more pages than its recipe limit. Bound a complete collection at 100 pages and report an explicit, honest failure if it cannot be read completely.
- [ ] **sell2wales-gb:** inspect the public page’s form-backed pager and determine whether its anonymous GET page state can be followed safely. Keep this case failed until every listed page is accounted for.
- [ ] **boamp-fr:** use the official API’s JSON envelope directly. Preserve its pagination metadata, validate that the full collection is covered, and confirm each advertised document link can be fetched before reporting serving. Replace the stale HTML recipe on retry so its retained single tender cannot be mistaken for a healthy read.
- [ ] **tenderned-nl:** identify the anonymous tender request made after the SPA initializes. Confirm that the result selector reads public tender rows and that the complete page chain is validated.
- [ ] **bekanntmachungsservice-de:** trace the public UI’s anonymous publication search and map the actual result records into the JSON recipe.
- [ ] **bosa-eprocurement-be:** trace the anonymous Tender Bulletin search request and validate tender-shaped records rather than configuration or setup responses.
- [ ] **vergabemarktplatz-nrw-de:** document and test the live redirect destination. Permit it only if it is the same public portal after its canonical HTTP-to-HTTPS or `www` redirect.
- [ ] **place-fr:** inspect current public results and identify a portal-issued notice ID and title for each sampled tender. Keep the portal failed if either field cannot be established.
- [ ] **public-contracts-scotland-gb:** configure the container’s certificate trust chain so standard TLS validation accepts the observed public certificate, then confirm the request succeeds in the container.

## Re-run and hand-in

- [ ] Restore the team’s P17 `testset/run_all.py` runner and record its version in this report.
- [ ] Re-run all ten public URLs in a clean database with the hand-in key and the configured shared spend cap.
- [ ] Require each successful portal to have a validated complete listing, truthful tender fields, healthy documents and a serving state. Preserve a plain failure reason otherwise.
- [ ] Compare `results.md` model costs with `llm_calls`, confirm no outstanding reservations, and update the observed run time and all results.
- [ ] Build and run the Linux container, then repeat the public-portal checks in that container.

## Known scope

The two mock regressions for detail-page 404 and 410 are covered by tests. Redirect following has fixture coverage. Downloading a document from another host has a mock test. Live B19 document delivery has not been demonstrated. Real-model repair on the mock, B1’s prompt changes, and the mock captcha check are also outside this B2 implementation.
