# Watchtower: mock judging report (Patterno track)

Judged 21 Nov 2026, commit `2938f15`, run as the brief says: `docker build`, `docker run --env-file .env`, fresh volume, then only `POST /portals`. Nobody steered the runs. Model spend for the whole session was **$0.41**.

## Scorecard (each criterion 1–5, 20% each)

| Criterion | Score | One-line verdict |
|---|:-:|---|
| Attach | **2** | Excellent when it works (UPOL: 15/15 tenders, 102 files, correct). It worked on 1 of 7 real portals, and on the team's own mock it fails or passes depending on the run. |
| Notice | **3** | Captcha, healing and last-good snapshots are right. Portals that never passed a read still publish their tenders, and expired tenders show as `open` when the deadline is missing. |
| Repair | **4** | Two layout breaks were healed in under a minute for about $0.015 each, with the same IDs, file paths and `lastChangedAt`. Only possible after a successful attach. |
| Dashboard | **4** | Clean, readable, works on mobile, honest reasons. A few labels contradict each other. |
| Approach | **3** | Good architecture (model learns, plain code reads, $0 refresh, cost ledger). Failures cost up to 30× a success, results vary by run, and the browser loads third-party trackers. |
| **Overall** | **3.2** | A solid platform with a weak first read. Fixing attach consistency would raise every other score. |

## What I tested

| Portal | Type | Result | Tenders / files | Model $ |
|---|---|---|---|---|
| Judge fixture "easy" (private, DE, 3 pages, ZIPs) | SSR | serving → layout v2 → healed → captcha → failed → recovered | 7 / 21 | 0.020 |
| Judge fixture "hard" (summary-page trap, numbered pager) | SSR | failed in both runs | 3 of 7 / 0 | 0.005 / 0.142 |
| Team mock `:8090` | SSR | **failed** (cached run) / **serving + healed** (uncached run) | 5 / 0 vs 5 / 8 | 0.027 / 0.020 |
| zakazky.upol.cz (CZ, E-ZAK) | SSR | **serving**, checked against source | 15 / 102 (134 MB) | 0.011 |
| platformazakupowa.pl (PL) | SSR | failed: "redirected outside the recipe's origin" | 0 | 0.049 |
| achatpublic.com (FR) | SSR + JS | failed: invalid XPath, no ID/title | 0 | 0.060 |
| evergabe-online.de (DE federal) | Wicket, cookie check | failed: next_page / list selector found nothing | 0 | 0.070 |
| simap.ch (CH) | SPA | failed: "listing URL was not observed" | 0 | 0.003 |
| doffin.no (NO) | SPA | failed: JS, no public feed found | 0 | 0 |
| marches-securises.fr (FR) | SPA | failed: JS, no public feed found | 0 | 0 |

API contract: every appendix rule I tried passes. Same URL gives the same ID, refresh during a run returns the same `runId`, unknown IDs return 404, DELETE returns 204 and there were **0 requests in the next 64 s**, `changedSince` works (even with an unencoded `+`), and runs show read/repair with `null` while running. Build: `docker build` passes in 1m52s with cache; the image is 3.9 GB. Tests: 352 passed, 19 skipped.

## Fix first (biggest score gain per hour)

### 1. Deadlines silently become null, and expired tenders then stay `open`. *(Attach + Notice)*
Reproduced in `brain/normalize.py::_deadline`:
```
'08.12.2026 10:00 Uhr (Ortszeit Berlin)'  -> None   # trailing text breaks the parse
'08.12.2026 10:00 Uhr'                    -> 2026-12-08 10:00+01:00
'09 Dec 2026, 10:00' with locale.tz=None  -> None   # the team mock: all 5 deadlines null
```
On my fixture **all 7 deadlines were null**, so `VG-2026-0398` (deadline 14 Nov) was served as **`open`**. The brief says a wrong value is worse than a missing one.
- Pull the first `dd.mm.yyyy hh:mm` / `d Mon yyyy, hh:mm` match out of the string before parsing, rather than parsing the whole cell.
- When `locale.tz` is null, infer it from the page (`<html lang>`, TLD, country in address) or ask the model once during attach. Add a health rule: "the listing shows clock times but 0% of deadlines parsed" fails the read.
- When the deadline is unknown, do not default to `open` for a tender whose detail page says it is closed. Add a fixture for this.

### 2. Attach gives different results on identical input, and the cache freezes a bad answer. *(Attach + Approach)*
On the same mock and the same commit: run 1 (`LLM_CACHE=1`, the README default) gave `failed`, with `id="/tender/MOCK-001"` and no detail link. Run 2 (`LLM_CACHE=0`) gave `serving` with 5 tenders and 8 files. With the cache on, **Relearn and Repair resend the identical prompt and get the same bad answer back for free**, so they cannot improve.
- Turn the cache off for relearn/repair (or add the attempt number or feedback to the cache key). Plan v3 already says README should default `LLM_CACHE=0`.
- Make attach try 2–3 candidates (temperature or a varied prompt) and keep the best by score: share of distinct IDs, fill rate, deadlines parsed, documents found, pages walked. Today the first candidate that passes the checks wins.

### 3. Pagination is dropped silently. *(Attach)*
My fixture shows `Seiten: 1 2 3` (numbered links, no "next" label). The recipe had `next_page: null` and read **3 of 7** tenders, and no check complained. The uncached run walked to page 2 and stopped there.
- If the listing has links back to the same path that differ only in a page-like query parameter (`seite`, `page`, `p`, `offset`), synthesize a next-page rule, or at least fail with "listing advertises N pages, read 1".

### 4. Two-hop documents (summary page → tender page → files). *(Attach)*
The track lists this as a weekly trap. On the hard fixture the model **found** the "Verfahrensseite" link but used it as `documents.link`, so the HTML page was "downloaded" and reported "3 of 3 document downloads failed". Let `detail` hold a chain of links (`detail.link` then `detail.next`), or when a "document" response is `text/html` on the same origin, follow it once and look for files there.

### 5. Failed portals publish tenders that were never verified. *(Notice)*
The mock portal page says *"kept from the last correct read while the portal is failed"* next to *"Last good read: never"*. `GET /portals/{id}/tenders` returns those 5 rows (null deadlines, URL-path IDs) to any client. A Patterno integration polling the API would ingest them.
- Only publish rows that came from a read that passed health checks. Until then `/tenders` should return `[]`, with the preview on the dashboard only.

## Fix next

- **Polite-rate leaks.** Redirect hops skip the spacing: `evergabe-online.de` 302→200 in 0.42 s, `achatpublic.com` in 0.17 s. The first browser sub-resource goes out 0.17 s after its page. Route redirect hops through `wait_turn`.
- **Browser loads trackers.** Discovery fetched Google Tag Manager, Microsoft Clarity, Matomo heatmaps, Skyra surveys and consent SDKs. Block third-party scripts and analytics hosts in Playwright (`route.abort`). That's faster, more polite, and avoids sending visits into someone's analytics.
- **File names.** UPOL file names are left URL-encoded (`2_Formul%C3%A1%C5%99%20nab%C3%ADdky.docx`): use `unquote()` on them. ZIP entries are garbled (`sevekn°_kridlo_konstrukün°` should be `severní_křídlo_konstrukční`): when the UTF-8 flag (bit 11) is unset, re-decode `cp437` → `cp852` (Central Europe) or try `cp1252`. Split archives (`.zip.001/.002`) are delivered as raw parts; that's acceptable, but say so.
- **Titles** carry the page's heading prefix (`Veřejná zakázka: …`). Fine per "as the portal states it", but on the listing the title has no prefix. Prefer the listing title.
- **Optional fields are rarely filled.** My fixture shows CPV, procedure type, contract nature and place of performance in a labelled table, and all four were missed. On UPOL, CPV and reference number were filled. Name the optional keys and their allowed values in the detail prompt.
- **Mock IDs.** `id = ".//td[1]/a/@href"` gave `/tender/MOCK-001`. It's stable, but it isn't the portal's identifier, which is shown on the detail page. Add a check: when an ID looks like a URL path, prefer a detail-page value that matches its last segment.
- **Failure cost.** The hard fixture spent $0.142 over 7 calls (3 GPT-5 repairs) before giving up; a success costs $0.004–0.03. Stop repairing once two candidates fail for the same reason.

## Dashboard notes (good overall)

Works: state chips, plain-language reasons, per-portal cost, run history, "Repair recipe" / "Relearn" buttons, sort by "needs attention", mobile layout.
Contradictions to fix:
- Failed cards say **"never read"** next to "5 tenders".
- Activity shows **"Read failed … read ok, but no tender documents were found"**.
- KPI "Tenders delivered **23** across 9 portals" next to "**1 of 9** portals deliver tenders". The 23 includes the unverified rows from failed portals.
- Deadline column says **"not shown"** when the portal does show it but parsing failed. Say "not parsed".
- "Monitoring Source listing (historical coverage unverified)" is jargon to an operator.
- The brief asks for "what the system did about it". Show per-run request count, duration and model $ in the run list. You already have the data.

## What would impress a judge in the video

1. Fresh volume → paste UPOL → `serving` with 15 tenders and 102 files, then open one file.
2. The easy-style fixture: break the layout → `healing` while the API still serves 7/21 → `serving` with identical IDs, for $0.015.
3. Captcha → `failed: blocked: captcha required`, no model spend, old data kept → lift the block → `serving`.
4. Refresh → 0 model calls, `lastChangedAt` unchanged.

Do **not** demo the team mock until items 1–2 above are fixed: in my first run it failed.

## Reproduce

Fixture: `fixture.py` (stdlib only), run with `python3 -I fixture.py 8091` (hard) or `... 8092 easy`, plus switches `/_v2`, `/_captcha`, `/_reset`. Ground truth and checker: `verify.py`. Both sit next to this report in `judge/`, with dashboard screenshots (`dash.png`, `upol.png`, `mock.png`). Move them into `testset/` to use them as regression fixtures.
