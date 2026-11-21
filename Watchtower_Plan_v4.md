# Watchtower Plan v4

**TokenBurners · Patterno track · Checkpoint at 03:00 CET, 21 November 2026**  
Status after the mock judging run (PR #25) and its fixes (PR #26): what is done from Erik's review, plan v3 and the judge's report, how the test set meets the brief's test-portal rules, and what is left until hand-in. Replaces plan v3.

## 1. Where we stand

| Signal | Verified at this checkpoint |
| --- | --- |
| Release candidate | main at 4456c8e (merge of [#26](https://github.com/yuriboulderingGIT/TokenBurners/pull/26)). CI passed on main: tests and the Linux amd64 image with its container contract. |
| Tests | 401 passed, 1 skipped (Windows only), including Erik's 14 review probes and the judge regressions. |
| Live API check | 45 of 45 contract rules pass against the app and the mock portal: attach, files and ZIPs, refresh, changedSince, login block, DELETE. No model used. |
| Mock judging | [#25](https://github.com/yuriboulderingGIT/TokenBurners/pull/25) ran the jury's procedure on 2938f15, before #26: **3.2 / 5** (Attach 2, Notice 3, Repair 4, Dashboard 4, Approach 3). The top five findings are fixed in #26 (section 6). |
| Real model | Judge session on 2938f15: $0.41 in total; two layout repairs healed in under a minute for about $0.015 each. #26 attached the judge's hard fixture from its URL alone: 7 of 7 IDs, 21 of 21 files, $0.004549. Not yet run on 4456c8e. |
| Public portals | Both runs predate #26. Erik's 10 test portals: 1 real success (Cyprus, 10 tenders, 4 files). The judge's 7 other real portals: 1 success (zakazky.upol.cz: 15 tenders, 102 files, checked against the source). |
| Deadline | Internal hand-in **10:30 CET**, organizer cutoff **11:00**. From 09:00 every merge needs a second reviewer. |

## 2. Landed since plan v3 (23:14)

| Change | What it does |
| --- | --- |
| [#20](https://github.com/yuriboulderingGIT/TokenBurners/pull/20) site-reading audit | One cookie jar per portal. Honors `<base href>`. One ID on two different notices fails the read. Retries a temporary error on the first fetch. Browser discovery shares cookies and headers both ways, answers replayed POSTs, reads the portal's API subdomain. The first read reuses the listing pages that attach validation already walked. |
| 8b2db8a, 36a2f24, 84f807f, e4d71ea | TED's gzip POST bodies no longer crash discovery. Public document links are recovered. Repair works for feeds on an API subdomain. Czech deadlines are read day-month. |
| 35d8f90 | Tenders without any document link report `failed: no documents` without a paid repair. |
| a890ad9, 1e4d855 | Erik's live 10-portal run and his algorithm review with offline probes. |
| [#21](https://github.com/yuriboulderingGIT/TokenBurners/pull/21) dashboard | Patterno light palette, clickable portal boxes with reload and delete, state filters, portal page tabs. |
| 4cd6104 page limit | A list longer than 100 pages fails with a clear reason and no more paid attempts. Slow JavaScript apps get time to load (TenderNed: 2,474 tenders in 55 s). A tender's own page is never its document. BOAMP test input bounded. |
| [#22](https://github.com/yuriboulderingGIT/TokenBurners/pull/22) Erik's P1 items | See section 3. |
| [#25](https://github.com/yuriboulderingGIT/TokenBurners/pull/25) mock judging | `judge/JUDGE_REPORT.md`, a private two-mode fixture portal (`judge/fixture.py`, with layout v2 and captcha switches), a checker and screenshots. |
| [#26](https://github.com/yuriboulderingGIT/TokenBurners/pull/26) judge fixes | Numbered pagers, the summary-to-notice hop, labelled IDs and deadlines, document coverage checked before publishing, fresh model answers for recovery, rate-limited redirects, tracker blocking, per-run requests, time and cost on the dashboard. See section 6. |

<!-- pagebreak -->

## 3. Erik's review: done and open

### P1 items: all done

| P1 finding | Status | How it works now |
| --- | --- | --- |
| Category pages pass as tenders | Done | Each row needs evidence that it is one notice. A single odd row is left out; the read fails when most rows are not notices. The NRW and Brandenburg cases fail. |
| A file is assumed unchanged at its URL | Done | Each read checks up to 30 known files again, stalest first. A changed file gets a new link; old links keep their bytes. A failed check serves the last verified file. Login or captcha still means `failed`. |
| Missing from the list means closed | Done, following the brief | The brief says `closed` once the portal no longer lists the tender, so that stays. Absence is recorded (`missing_since`, shown on the portal page). |
| A latest-results window treated as everything | Done | The listing's scope is recorded and shown. In a latest window a missing tender stays open until its page or its deadline closes it. A model may not add a buyer, category, CPV, region or keyword filter. |
| Manual refresh cannot repair after a failure | Done | Refresh of a failed portal may start a repair or a new attach at most once an hour. Repair and Relearn buttons on the portal page are not limited. |
| Overlapping pages look complete | Done | Coverage counts distinct IDs; fewer than advertised fails the read. |
| A search recipe does not identify its operation | Done | Operation and filter digests without session data; an ambiguous match triggers a repair. |
| Requests to private networks (public deployments) | Done, opt-in | `PUBLIC_DESTINATIONS_ONLY=1`. Off by default because the jury's private portals may run on a private network. |

### P2 items: mostly open (plan: after hand-in, disclose in the README)

| P2 finding | Status | Note |
| --- | --- | --- |
| A genuinely empty listing is always unhealthy | Open | A portal with zero open tenders reports failed. |
| Rows lost in normalization (80% ID rate passes) | Open | Probe reproduces. |
| List and detail sources cannot be combined | Open | A buyer only in the list is lost; a JSON list cannot open HTML details. |
| Unknown status becomes `open`; date-only deadlines are dropped | Partly | #26: a closed status on the source now wins over an unknown deadline. Date-only deadlines are still `null`. |
| Search replay resets an observed offset to 0 | Open | Probe reproduces. |
| 100-page ceiling | Partly | Reported honestly, no paid retries; no backfill. |
| Little room to recover from a bad navigation choice | Open | |
| Browser discovery supports few interactions | Partly | Slow apps, gzip POSTs and trackers handled; no typed search, filter or pager actions. |
| Feed discovery and replay differ (headers, session) | Open | Code-level risk, not reproduced live. |
| Document and notice coverage are mixed | Partly | Own reasons for missing and failed documents; unverified documents block publishing (#26). |
| Recovery is narrow and expensive | Partly | No paid repair for page limit or missing documents; refresh-triggered model use hourly; identical failed proposals stop before the strong model (#26). |

## 4. Plan v3 items

| v3 item | Status | Evidence or gap |
| --- | --- | --- |
| P0-A Deliver the right notices and state | Done | #20, #22, 35d8f90, 4cd6104, #26 (unverified reads are not published). A verified empty result is unsupported. |
| P0-B Portal sessions and browser reads | Done | #20. `output/site_read_reproductions.py` needs Microsoft Edge for its browser probes; the copies in `tests/` pass. |
| P0-C Identity and bytes through repair | Done | Tested offline; the judge's real-model repairs kept the same IDs, file paths and `lastChangedAt`. |
| P1-D, P1-E, P1-F | Done | #20 |
| P1-G Model-assisted checking | Open | Our checks are deterministic. Recommendation: say in the README that structural checks decide and the model learns and repairs. |
| P1-H Verified handoffs | Partly | API subdomain done; documents on another host are still dropped. |
| P2 Targets and docs | Done | BOAMP bounded; `LLM_CACHE` defaults to 0 in the code, `.env.example` and the README (#23, #26). |

## 5. Test set and traps from the brief

The brief's "Finding test portals" page sets five rules for our test set (`testset/portals.yaml`, 10 portals, owner A2) and names four traps.

| Brief's rule for the test set | Status | Detail |
| --- | --- | --- |
| Each portal has a public list and 5+ open tenders whose documents download without login | Open | Not checked for any portal in the set. BOAMP has no documents. Proven elsewhere: zakazky.upol.cz (15 tenders, 102 files, judge), Masaryk E-ZAK (482 files on 28 tenders), Berlin, Charité. None of them is in the set. |
| 4+ countries and 3+ portal software packages | Done | 7 countries, 7 packages. |
| A mix of server-built pages, JavaScript apps with a JSON API, and multi-step downloads | Partly | 7 server-rendered, 2 JSON. The only multi-step portal, PLACE, was never checked directly. The judge's hard fixture covers summary-to-notice locally. |
| 1–2 portals whose documents need a login or a captcha | Partly | Sell2Wales needs registration for attachments. Captcha only in the mock and the judge fixture, not on a real portal. |
| A few portals stay unseen until the final test | Open | `testset/secret.yaml` does not exist on mihaly's machine. The judge fixture is in the repo now, so it is no longer unseen. |

| Trap from the brief | Status | How the code handles it |
| --- | --- | --- |
| Notice links lead to a summary page, not the tender | Done | #26 follows one explicitly labelled link from a summary to the full public notice and looks for its documents there (judge hard fixture: 7 IDs, 21 files). Shared addresses and missing files are still caught as before. |
| Deadlines are shown in local time | Done | An offset on the page is kept, else the zone of the recipe, country or domain. Text after the time no longer breaks parsing (#26). Clock times shown but no deadline parsed fails the read. An unknown zone gives `null`. |
| ZIP files can be larger than 1 GB | Done | Downloads stop at 250 MB, which the brief allows us to leave out. Unpacking capped at 1 GB and 1,000 files per archive. |
| A portal shows a captcha, yet the documents download | Done | A page counts as blocked only when it shows no content. Each file is judged by its own response. |

<!-- pagebreak -->

## 6. Mock judging (PR #25) and the fixes (PR #26)

| Judge's finding | Status | On main now |
| --- | --- | --- |
| Deadlines silently become `null`, so expired tenders show `open` | Done | Trailing text handled; a read showing clock times with no parsed deadline fails; a closed source status wins. |
| Attach varies between runs; the cache replays a bad answer | Partly | Repair, relearn and retries ask the model afresh; cache off by default. Attach still keeps the first candidate that passes instead of scoring 2–3. |
| Numbered pagination dropped silently (3 of 7 read) | Done | Numbered pagers with the same filters are followed (7 of 7). |
| Summary page, then tender page, then files | Done | One labelled hop to the full notice (21 of 21 files). |
| Failed portals publish never-verified tenders | Done | A read with unverified documents is not published; failed first reads keep nothing. |
| Redirect hops and browser sub-resources skip the spacing | Done | Every hop waits its turn. |
| Discovery loads trackers | Done | Known analytics and consent hosts are blocked. |
| URL-encoded and garbled file names | Done | Names decoded from the header, the URL and the ZIP's encoding. |
| Mock IDs are URL paths; optional fields rarely filled | Done | A path ID is replaced by the matching labelled ID; the prompt names the optional fields and their values. |
| Failures cost up to 30× a success | Partly | Identical failed proposals stop before the strong model. Not re-measured. |
| Dashboard contradictions and missing run details | Done | Labels fixed; each run shows requests, duration and model cost. |
| Attach works on 1 of 7 real portals | Open | Re-run needed on 4456c8e (task 4). Failures seen: redirect outside the recipe's origin (platformazakupowa.pl), invalid XPath (achatpublic.com), no selector match (evergabe-online.de), listing not observed (simap.ch), no public feed (doffin.no, marches-securises.fr). |
| Listing title vs detail heading prefix | Open | Minor; titles follow "as the portal states it". |

<!-- pagebreak -->

## 7. What is left before hand-in

Feature work is over. Only fixes with a test, and only if they keep the 45-rule contract and CI green.

| # | Task | Owner | Status | Done when |
| --- | --- | --- | --- | --- |
| 1 | Real-model proof on the release SHA: mock attach, then layout v2 and repair (about $0.04) | B1 | Partly | `python -m brain.proof --live --only mock` passes on the release SHA; cost recorded. Needs the budget owner's OK. |
| 2 | Check every test portal for 5 open tenders whose documents download; replace those that fail | A2 | Open | Each entry records tenders checked, files downloaded and the date. Add zakazky.upol.cz first; then Masaryk, Berlin, Charité. No model calls. |
| 3 | Held-out set and one real captcha portal | A2 | Partly | `testset/secret.yaml` (never committed) holds 2–3 checked portals nobody runs before the final test; one real captcha portal in the public set. |
| 4 | Re-run the public test set and the judge's 7 real portals on 4456c8e | A2 with Erik | Open | Serving count, tenders, files and cost recorded next to the 1-of-10 and 1-of-7 baselines. |
| 5 | Demo portal | B2 | Done | zakazky.upol.cz, checked against the source by the judge. Confirm it again in task 4. |
| 6 | Fix the most common real-portal failure from task 4 | B1, B2 | Open | One small PR with a fixture, for example the origin redirect. Skip if nothing is safe before 07:30. |
| 7 | README update | A2 | Partly | Cache default fixed. Still to add: cost from tasks 1 and 4, and plain known limits (section 3 P2, P1-G, section 6 open items). |
| 8 | Video, 3:00 maximum | A1 | Open | The judge's outline: UPOL from a fresh volume to `serving` and one file; the fixture's layout break healing while the API keeps serving; captcha to `failed` and back; a refresh with zero model calls. |
| 9 | Lock the release SHA | Release lead | Open | 08:30. After that, merges only with a second reviewer and a green CI. |
| 10 | Submit | Release lead | Open | Repo link, video, README, judging key handed over privately, by 10:30. |


<!-- pagebreak -->

## 8. Schedule

| CET · 21 Nov | Owner | Exit condition |
| --- | --- | --- |
| 03:00–07:30 | B1, B2, A2 | B1: task 1 when the budget owner approves, then task 6 with B2. A2: tasks 2 and 3, then 4. Small fixes only, each with a test. |
| 07:30–08:30 | A2, A1 | Dashboard screenshots, demo portal confirmed, README numbers (task 7). |
| 08:30–09:00 | Release lead, video owner | Release SHA locked, video cut. |
| 09:00–10:30 | Release lead | Clean image built from the SHA, README and run command checked, submission sent. |
| 11:00 | Organizer | Submissions close. |

**Cost on record.** Mock attach $0.005485, repair $0.028319, ordinary refresh $0 (20 Nov). Cyprus attach $0.019190. Erik's 10-portal run $0.260324 in total. TenderNed attach $0.0017. Judge session $0.41 in total: UPOL attach $0.011, layout repairs about $0.015 each; the hard fixture cost $0.142 failing before #26 and $0.004549 succeeding after it. Label every number with its data folder and SHA when it goes into the README.

**Behaviors to know for the demo.** A tender that leaves a normal listing turns `closed` at once. A read whose documents cannot be verified is not published. Refreshing a failed portal calls the model at most once an hour. Document checks are bounded to 30 files per read. The image is 3.9 GB and builds in about 2 minutes with cache.

## 9. Briefing for coding agents

Paste this before any task:

~~~text
Repo: github.com/yuriboulderingGIT/TokenBurners (Patterno "Watchtower" track). Hand-in 10:30 CET Sat 21 Nov.
One container on :8080 (FastAPI + dashboard). API contract: Patterno brief appendix. Tests: python -m pytest -q.
Rules: all HTTP through app/fetcher.py, all model calls through brain/llm.py. Max 1 request/s per portal.
No logins, no captcha solving. Never commit .env, data/ or testset/secret.yaml.
Feature freeze: fix only what has a failing test or probe; keep the 45-rule contract and CI green.
Keep these decisions: unlisted tenders become closed (except latest-results windows), the private-address
guard is opt-in (PUBLIC_DESTINATIONS_ONLY=1), API times use whole seconds, unverified reads are never published.
Judge fixture for regressions: python3 -I judge/fixture.py 8092 easy (or 8091 hard), checker judge/verify.py.
One small PR per fix with the test that proves it; from 09:00 a second reviewer must approve.
~~~

**Sources.** [Judge report](judge/JUDGE_REPORT.md), [Erik's review](output/watchtower-algorithm-review-2026-11-21.md) and [probes](output/watchtower-review-probes.py), [live results](output/live-site-results-2026-11-21.md), [site-reading audit](output/site-reading-audit.md), [plan v3](Watchtower_Plan_v3.md), [README](README.md), [DEVELOPING.md](DEVELOPING.md).
