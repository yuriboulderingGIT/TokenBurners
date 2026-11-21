# Watchtower Plan v4

**TokenBurners · Patterno track · Checkpoint at 01:55 CET, 21 November 2026**  
Status after PR #22, what is done from Erik's review and plan v3, and what is left until hand-in. Replaces plan v3.

## 1. Where we stand

| Signal | Verified at this checkpoint |
| --- | --- |
| Release candidate | main at 2938f15 (merge of [#22](https://github.com/yuriboulderingGIT/TokenBurners/pull/22)). CI on the identical tree (9cca568): tests passed and the Linux amd64 image passed its container contract. |
| Tests | 384 passed, 1 skipped (Windows only). This includes Erik's 14 review probes. |
| Live API check | 45 of 45 contract rules pass against the app and the mock portal: attach, files and ZIPs, refresh, changedSince, login block, DELETE. No model used. |
| Real model | Not run since PRs #20 to #22. Last proof (20 Nov): mock attach $0.005485, layout repair $0.028319, ordinary refresh $0. |
| Public portals | Erik's fresh run of the 10 test portals at 00:16, before #22: 1 real success (Cyprus, 10 tenders, 4 files), 2 false successes (NRW, Brandenburg: 44 category links each), 7 failed. #22 now rejects those category rows. |
| Deadline | Internal hand-in **10:30 CET**, organizer cutoff **11:00**. From 09:00 every merge needs a second reviewer. |

## 2. Landed since plan v3 (23:14)

| Change | What it does |
| --- | --- |
| [#20](https://github.com/yuriboulderingGIT/TokenBurners/pull/20) site-reading audit | One cookie jar per portal. Honors `<base href>`. One ID on two different notices fails the read. Retries a temporary error on the first fetch. Browser discovery shares cookies and headers both ways, answers replayed POSTs, reads the portal's API subdomain. The first read reuses the listing pages that attach validation already walked (half the GETs). |
| 8b2db8a | TED's gzip-compressed POST bodies no longer crash browser discovery. |
| 36a2f24, 35d8f90 | Public document links are recovered. A read that never opened tender pages and found no files reports `failed: no documents` without a paid repair. |
| 84f807f | Repair works for feeds on the portal's API subdomain. |
| e4d71ea | Czech deadlines are read day-month. |
| a890ad9, 1e4d855 | Erik's live 10-portal run and his algorithm review with 13 offline probes. |
| [#21](https://github.com/yuriboulderingGIT/TokenBurners/pull/21) dashboard | Patterno light palette, clickable portal boxes with reload and delete, state filters, portal page tabs. |
| 4cd6104 page limit | A list longer than 100 pages fails with a clear reason and what to do. It never publishes part of a list and makes no more paid attempts. Slow JavaScript apps get time to load (TenderNed: 2,474 tenders replayed in 55 s). A tender's own page is never treated as its document. BOAMP test input is bounded. |
| [#22](https://github.com/yuriboulderingGIT/TokenBurners/pull/22) Erik's P1 items | See section 3. |

<!-- pagebreak -->

## 3. Erik's review: done and open

### P1 items: all done

| P1 finding | Status | How it works now |
| --- | --- | --- |
| Category pages pass as tenders | Done | Each row needs evidence that it is one notice (notice links, context, buyer, deadline or files). A single odd row is left out; the read fails when most rows are not notices. The NRW and Brandenburg cases fail. |
| A file is assumed unchanged at its URL | Done | Each read checks up to 30 known files again, stalest first (ETag, Last-Modified, or a fresh download after 15 min; a full download after 24 h). A changed file gets a new link; old links keep their bytes. A failed check serves the last verified file. Login or captcha still means `failed`. |
| Missing from the list means closed | Done, following the brief | The brief says `closed` once the portal no longer lists the tender, so that stays. Absence is now recorded (`missing_since`, shown on the portal page). Exception below. |
| A latest-results window treated as everything | Done | The listing's scope is recorded and shown (latest window, open opportunities, filtered, archive). In a latest window, a missing tender stays open until its own page shows it closed or withdrawn, or its deadline passes. A model may not add a buyer, category, CPV, region or keyword filter; attach then stays on the requested listing. |
| Manual refresh cannot repair after a failure | Done | Refresh of a failed portal may start a model repair, or a new attach, at most once an hour (`REFRESH_REPAIR_MINUTES`). The portal page has Repair and Relearn buttons, which are not limited. |
| Overlapping pages look complete | Done | Coverage counts distinct tender IDs. Fewer than the portal advertises fails the read. |
| A search recipe does not identify its operation | Done | The saved search has a digest of the observed operation and of its filters, with no session data. Two matching searches trigger a repair instead of a guess. A repair keeps the filters that select notices but may change sort, paging and query shape. |
| Requests to private networks (public deployments) | Done, opt-in | `PUBLIC_DESTINATIONS_ONLY=1` checks every resolved address for portal pages, redirects, feeds, files and browser traffic. Off by default because the jury's private portals may run on a private network. |
| API subdomain repair (historical) | Done | Fixed in 84f807f before the review was merged. |

### P2 items: mostly open (plan: after hand-in, disclose in the README)

| P2 finding | Status | Note |
| --- | --- | --- |
| A genuinely empty listing is always unhealthy | Open | Probe still reproduces. A portal with zero open tenders reports failed. |
| Rows lost in normalization (80% ID rate passes) | Open | Probe still reproduces. |
| List and detail sources cannot be combined | Open | Two probes reproduce: a buyer only in the list is lost; a JSON list cannot open HTML details. |
| Unknown status becomes `open`; date-only deadlines are dropped | Open | Probe reproduces. |
| Search replay resets an observed offset to 0 | Open | Probe reproduces. |
| 100-page ceiling | Partly | 4cd6104 reports it honestly and stops paying; no backfill or checkpoints. |
| Little room to recover from a bad navigation choice | Open | |
| Browser discovery supports few interactions | Partly | Slow apps and gzip POSTs fixed; no typed search, filter or pager actions. |
| Feed discovery and replay differ (headers, session) | Open | Code-level risk, not reproduced live. |
| Document and notice coverage are mixed | Partly | Missing and failed documents now get their own reason; no multi-step download chains. |
| Recovery is narrow and expensive | Partly | Page limit and no-documents states skip paid repair; refresh-triggered model use is hourly. No failure classification yet. |

## 4. Plan v3 items

| v3 item | Status | Evidence or gap |
| --- | --- | --- |
| P0-A Deliver the right notices and state | Done | Duplicate IDs (#20), category rows and coverage (#22), missing documents (35d8f90), page limit (4cd6104), scope on the dashboard (#22). A verified empty result is still unsupported (Erik P2). |
| P0-B Portal sessions and browser reads | Done | #20. The copies of the audit probes in `tests/test_site_reading_audit.py` pass. `output/site_read_reproductions.py` launches Microsoft Edge, so its 6 browser probes only run where Edge is installed. |
| P0-C Identity and bytes through repair | Done offline | Atomic repair, last good snapshot, scope check and file revisions are tested. A real-model repair on the release SHA is still open (section 5). |
| P1-D Initial transient retry | Done | #20 |
| P1-E Effective base URL | Done | #20 |
| P1-F Reuse one attach traversal | Done | #20: the first read reuses validated listing pages. |
| P1-G Model-assisted checking | Open | The track asks the model to learn, check and repair; our checks are deterministic. Recommendation: do not add it now. Say in the README that structural checks decide, and that the model learns and repairs. |
| P1-H Verified handoffs | Partly | API subdomain GET discovery and repair done (#20, 84f807f). Documents on another host are still dropped. |
| P2 Targets and docs | Done | BOAMP bounded (4cd6104). README's `LLM_CACHE` default corrected to 0 in this update. |

## 5. What is left before hand-in

Feature work is over. Only fixes with a test, and only if they keep the 45-rule contract and CI green.

| # | Task | Owner | Done when |
| --- | --- | --- | --- |
| 1 | Real-model proof on the release SHA: mock attach, then layout v2 and repair (about $0.04) | B1 | `python -m brain.proof --live --only mock` passes; cost recorded. Needs the budget owner's OK. |
| 2 | Re-run the 10 public portals on the release SHA | A2 with Erik (ran the last one) | NRW and Brandenburg fail honestly instead of serving categories; Cyprus still serves with files; spend recorded. |
| 3 | One fresh public attach for the demo | B2 | Cyprus or TenderNed: five open tenders checked by hand against the source, files downloadable. |
| 4 | README update | A2 | Measured cost from tasks 1 and 2; known limits from section 3 (P2) and P1-G stated plainly. |
| 5 | Video, 3:00 maximum | A1 | Not in the repo yet. Show URL-only attach, a tender with a file, a layout change healing, and a truthful blocked state. |
| 6 | Lock the release SHA | Release lead | 08:30. After that, merges only with a second reviewer and a green CI. |
| 7 | Submit | Release lead | Repo link, video, README, judging key handed over privately, by 10:30. |
| 8 | Optional: make `output/site_read_reproductions.py` use Chromium | Anyone | Its browser probes run on macOS and Linux. |

**Behaviors to know for the demo.** A tender that disappears from a normal listing turns `closed` at once. Refreshing a failed portal repeatedly calls the model at most once an hour. Document checks are bounded to 30 files per read. Set `PUBLIC_DESTINATIONS_ONLY=1` only for a public deployment.

## 6. Schedule

| CET · 21 Nov | Owner | Exit condition |
| --- | --- | --- |
| 02:00–07:30 | B1, A2 | Tasks 1 and 2 when the budget owner approves. Small fixes only, each with a test. |
| 07:30–08:30 | A2, A1 | Dashboard screenshots, demo portal checked (task 3), README numbers (task 4). |
| 08:30–09:00 | Release lead, video owner | Release SHA locked, video cut. |
| 09:00–10:30 | Release lead | Clean image built from the SHA, README and run command checked, submission sent. |
| 11:00 | Organizer | Submissions close. |

**Cost on record.** Mock attach $0.005485, mock repair $0.028319, ordinary refresh $0. Cyprus attach $0.019190 (10 tenders, 4 files). Erik's 10-portal run on 21 Nov cost $0.260324 in total. TenderNed attach $0.0017 (4cd6104). Label every number with its data folder and SHA when it goes into the README.

## 7. Briefing for coding agents

Paste this before any task:

~~~text
Repo: github.com/yuriboulderingGIT/TokenBurners (Patterno "Watchtower" track). Hand-in 10:30 CET Sat 21 Nov.
One container on :8080 (FastAPI + dashboard). API contract: Patterno brief appendix. Tests: python -m pytest -q.
Rules: all HTTP through app/fetcher.py, all model calls through brain/llm.py. Max 1 request/s per portal.
No logins, no captcha solving. Never commit .env, data/ or testset/secret.yaml.
Feature freeze: fix only what has a failing test or probe; keep the 45-rule contract and CI green.
Do not undo these brief-driven decisions from PR #22: unlisted tenders become closed (except latest-results
windows), the private-address guard is opt-in (PUBLIC_DESTINATIONS_ONLY=1), API times use whole seconds.
One small PR per fix with the test that proves it; from 09:00 a second reviewer must approve.
~~~

**Sources.** [Erik's review](output/watchtower-algorithm-review-2026-11-21.md) and [probes](output/watchtower-review-probes.py), [live results](output/live-site-results-2026-11-21.md), [site-reading audit](output/site-reading-audit.md), [plan v3](Watchtower_Plan_v3.md), [README](README.md), [DEVELOPING.md](DEVELOPING.md).
