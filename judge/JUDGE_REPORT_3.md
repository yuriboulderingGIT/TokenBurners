# Watchtower: third judging

Judged 21 Nov 2026 ~04:20 CET at commit `ff45d91` (latest `main`). Same method as [report 1](JUDGE_REPORT.md) and [report 2](JUDGE_REPORT_2.md): fresh volume, `LLM_CACHE=0`, URL-only attach, nobody steering. Tests: **466 passed** inside the image. Locally, 2 browser tests fail without Chromium; one of them should skip like the others. Model spend this round: **$0.28**.

**New this round:** a third private fixture, [`fixture_nl.py`](fixture_nl.py), written after the latest PRs so the team hasn't seen it. It's a Dutch **JavaScript app**: an empty HTML shell, tenders from `/api/v1/aanbestedingen?pagina=N` (`"totaal": 11`), each tender's files from a second API call, and local-time deadlines without an offset (the footer says "Nederlandse tijd" in words). Built-in checker: `python3 -I fixture_nl.py check tenders.json`.

## Scorecard

| Criterion | R1 | R2 | **R3** | Why |
|---|:-:|:-:|:-:|---|
| Attach | 2 | 2 | **3** | UPOL now serves correctly in 3 min (18 open tenders, all files). All three known fixtures are perfect. The first JS app was ever discovered (Dutch fixture), but it reads 5 of 11 tenders and no files. |
| Notice | 3 | 3 | **4** | False successes are gone (achatpublic is now an honest `failed`). Login walls on documents are caught on the next read. Captcha ✓, old data kept ✓. Only gap: a required field that's 100% null on a first read (UPOL buyer) doesn't block `serving`. |
| Repair | 4 | 4 | **4** | The French layout change healed in about a minute, 99/99 correct afterwards, $0.033. |
| Dashboard | 4 | 4 | **4** | Progress messages ("Checking documents: 10 of 79 notices") help. No regressions seen. |
| Approach | 3 | 3 | **3.5** | Politeness is now clean (no gap under 1 s on any host, no trackers). Per-run request counts, and rejected recipes are logged in the run notes. Still: hand-written label lists, and failed attaches cost 4–6× a successful one. |
| **Overall** | 3.2 | 3.2 | **3.7** | The first real improvement in the score: fixes now generalize to a real portal (UPOL) and to Notice in general. |

## Results

| Portal | R2 | **R3** | Checked against source |
|---|---|---|---|
| **Judge NL (JS app, unseen)** | – | **failed**, honestly (nothing published) | Found the JSON API, every field mapped, `Europe/Amsterdam` inferred. Missed paging (5/11) and the detail API (0 files) |
| Judge FR | failed ×2 | **serving 11/44** | 99/99 ✓; repair 99/99 ✓; login → `failed` ✓; captcha → `failed` ✓ (the team had this fixture) |
| Judge DE "hard" | serving 7/21 | **serving 7/21** | ✓ |
| Team mock | serving | **serving 5/8** | Documents behind a login → `failed: login required` on the next read ✓ |
| zakazky.upol.cz | 0 after 33 min | **serving in ~3 min** | 18 open (13 before: page 2 is now included), files only for open tenders, all deadlines ✓, names decoded ✓. **`buyerName` null on 79/79** |
| achatpublic.com | false `serving` | **failed** (honest) | CSS buyer no longer accepted ✓ |
| platformazakupowa, simap, evergabe-online, doffin, marches-securises | failed | failed | – |

Real portals: **1 of 7 serving**, now correctly. The other 6 fail honestly.

## Fixed since report 2 ✓

1. UPOL archive crawl: bounded and fast, and files are fetched for open tenders only.
2. Garbage field values: rejected before `serving`.
3. Login wall on documents: caught on the next read, both as a login page and as a "previously public file".
4. Rejected attach candidates are saved in the run notes.
5. Politeness: the minimum gap is 1.11 s on every host, and no analytics or tracker requests.

## Fix next (in order)

### 1. JSON APIs: follow paging and use the detail endpoint. *(Attach)*
The Dutch recipe has `next_page: null` although the response says `"totaal": 11, "pagina": 0` and the URL has `?pagina=0`. It also has `detail.link: null`, even though the browser opened `/aanbesteding/<uuid>`, which loads `/api/v1/aanbestedingen/<uuid>` with the `documenten` array.
- Reuse the HTML page-parameter logic for JSON: if the URL has a page-like parameter and the body has a total (`totaal`, `total`, `totalElements`, `count`), step the parameter until the total is reached.
- During discovery, open one tender page and record the API call it makes. If it contains the list item's ID, that's `detail.link` (`/api/v1/aanbestedingen/{id}`).

### 2. Required field 100% null on a first read. *(Attach, Notice)*
UPOL serves 79 tenders with `buyerName: null`, while every notice page says `Úřední název: Univerzita Palackého v Olomouci` (round 1 had it). Health only flags a field that *used to* be filled. On a first read, make one cheap targeted model call: "map `buyerName` from this notice page", then validate it on the sample.

### 3. Deadline parser still misses common formats. *(Attach)*
The French fixture passes (the recipe learned its format), but `brain/normalize.py::_deadline` still returns None for:
```
'15/12/2026 à 12h00'              '15 décembre 2026 à 12:00'
'15 december 2026 om 12:00 uur'   '09 Dec 2026, 10:00' (no locale)
```
A generic pre-clean (`HHhMM` → `HH:MM`, drop connector words like `à`, `om`, `um`, `at`, drop `uur`/`Uhr`, drop the leading label) covers most European portals in one place.

### 4. Cost of failure. *(Approach)*
Successful attaches cost $0.006–0.010; failed ones $0.04–0.06 (evergabe-online $0.062, platformazakupowa $0.056). Stop after two candidates fail for the same reason.

## Video

The demo set is now strong. Show UPOL live (real portal, 3 minutes, 18 open tenders with files), the hard or French fixture with a layout break → healing → serving, then captcha and login → `failed` with the old data kept. Show achatpublic as the honest failure.
