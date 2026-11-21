# Watchtower: second judging (after PR #26)

Judged 21 Nov 2026 ~03:00 CET at commit `4456c8e` (latest `main`). Same method as [report 1](JUDGE_REPORT.md): fresh volume, `.env` with the new default `LLM_CACHE=0`, URL-only attach, nobody steering. Tests: 367 passed, 21 skipped. Model spend this round: **$0.25**.

**New this round:** a second private fixture, [`fixture_fr.py`](fixture_fr.py), written after PR #26 so the team has never seen it. It's French: card markup, an icon-only pager (`‹ 1 2 3 ›`), facts in `<dl>`, two hops to files, dates like `15/12/2026 à 12h00`, one closed and one withdrawn notice. It has a built-in checker (`python3 -I fixture_fr.py check tenders.json`). This is the fairest test of whether the fixes generalize.

## Scorecard

| Criterion | Before | Now | Why |
|---|:-:|:-:|---|
| Attach | 2 | **2** | Both old fixtures and the mock now pass every check. The **unseen French fixture fails** on two fresh attempts. UPOL regressed: it now crawls the whole archive and publishes nothing for over 30 minutes. |
| Notice | 3 | **3** | Unverified reads are no longer published ✓, captcha is correct ✓. New false success: achatpublic `serving` with 63 KB of CSS as the buyer name. A login wall on documents goes unnoticed for up to 15 min. |
| Repair | 4 | **4** | Both layout breaks healed in about 1 minute for $0.014–0.015, with the same IDs. Repair no longer replays cached answers ✓. |
| Dashboard | 4 | **4** | The contradictions are fixed ("no verified read yet", "22 from 3 serving portals"). One portal page renders 51,725 px tall because of the CSS buyer name. |
| Approach | 3 | **3** | Per-run request counts ✓, spacing now measured from the previous response ✓, attach as cheap as $0.004. But part of the fixes are hand-written German/English word lists, which won't scale to 29 countries. |
| **Overall** | 3.2 | **3.2** | The same score with a different shape: the known fixtures are fixed, but an unseen one and a real portal break in new ways. |

## Results

| Portal | Before | Now | Checked against source |
|---|---|---|---|
| Judge fixture "hard" (DE) | failed | **serving 7/21** | 63/63 checks ✓ (the team had this as a test case) |
| Judge fixture "easy" (DE) | serving, null deadlines | **serving 7/21** | 63/63 ✓ |
| Team mock | failed / serving (varied) | **serving 5/8** | ✓, but partly because the mock was edited to declare `lang="en-IE"` and "local time in Europe/Dublin" |
| **Judge fixture FR (unseen)** | – | **failed ×2** | "sample tenders need a portal-issued ID and a title"; $0.08 over 8 calls |
| zakazky.upol.cz (CZ) | serving 15/102 | **integrating, 0 published after 33 min** | 668 requests, 729 MB downloaded, page 18 of max 100 (deleted by me) |
| achatpublic.com (FR) | failed | **serving 10/23, false success** | buyer = CSS debris (up to 63,367 chars), no dates, files are Q&A attachments |
| platformazakupowa.pl, simap.ch, evergabe-online.de, doffin.no, marches-securises.fr | failed | failed | – |

**Correction to report 1:** UPOL's "15/15" counted page 1 only. UPOL lists open and finished tenders together, newest first, and page 2 still has open ones (deadlines 24 Nov and 7 Dec 2026). The first run missed them.

## What PR #26 got right

- Unverified first reads are no longer published (`/tenders` stays empty until a read passes the checks).
- Repair and relearn bypass the cache, and the default is `LLM_CACHE=0`.
- Numbered and icon-only pagers are followed (`seite`, `start`, `page` …). The FR fixture walked all 3 pages.
- Request spacing now starts when the previous response arrives, and redirects and file downloads use the same slot.
- `Content-Disposition` file names are decoded (RFC 2231). Split ZIPs are documented.
- Runs record `request_count`, and run cost comes from the call ledger.
- The dashboard wording contradictions are gone.

## Fix next (in order)

### 1. UPOL: the first read crawls the whole archive. *(Attach, Approach)*
The new pagination support follows UPOL's pager through 2025 notices and downloads every file of every finished tender. The portal stays `integrating` with 0 tenders, so a judge would see nothing for an hour.
- **Stop paging when a whole page is closed or past its deadline** (the listing is newest first). Also stop when a page only repeats known IDs.
- Don't download files for tenders that are already closed on the first read. The brief asks for every *open* tender's documents.
- Publish the tenders verified so far after every N pages, so a long first read still delivers something.

### 2. Reject garbage field values before `serving`. *(Notice)*
achatpublic passed the health check with `buyerName` = `"Retour .sdmCardConsult__bloc …"`, up to 63,367 characters, and also broke the portal page layout. Add a generic plausibility check in `app/health.py`:
- Fail if a short field (`title`, `buyerName`, `referenceNumber`) exceeds ~500 characters, or contains CSS/JS tokens (`{`, `}`, `.class__`, `function(`).
- Fail if one `buyerName` repeats across very different tenders *and* looks like markup.
- A wrong value is worse than a missing one: null it, and fail the read if this affects most rows.

### 3. Generalize instead of adding words. *(Attach, Approach)*
Parts of PR #26 match my first fixture's exact wording: the labels `Kennung`, `Art`, `Schlusstermin`, `Az.`, the pattern `(Ortszeit …)`, and the link text `verfahrensseite`. The unseen French fixture shows the limit. `notice_fields.py` has no French labels, and the deadline parser still returns None for:
```
'15/12/2026 à 12h00'                     lang=fr          -> None
'15/12/2026 à 12h00 (heure de Paris)'    lang=fr, FR      -> None
'Date limite : 15 décembre 2026 à 12:00' tz=Europe/Paris  -> None
'09 Dec 2026, 10:00'                     no locale        -> None
```
Generic fixes:
- Before parsing: turn `HHhMM` into `HH:MM`, drop the leading label (everything before the first digit), and drop connector words (`à`, `um`, `om`, `at`, `o`).
- In the attach prompt, ask the model to return a sample `deadline_example` and its parsed value, then use that to pick the date order and zone. This is the LLM learning the format, rather than a word list per country.
- The model already sees the notice page: let it map `<dl>`/`<th>` labels to fields in the recipe. Keep `notice_fields.py` as a fallback, not the main path.

### 4. Notice a login wall on documents. *(Notice)*
With the mock's documents moved behind a login (`docs=login`), the next read made 7 requests, touched no document link, and stayed `serving`, "verified 8 documents". Files are only re-checked after 15 minutes. Spend one request per read on a cheap check of one document link per portal (HEAD or a `Range: bytes=0-0` GET). If it now returns a login page, go `failed: login required`.

### 5. Save rejected attach candidates. *(Approach)*
The French fixture failed after 4 model calls, and the rejected recipes aren't stored anywhere. Saving the last rejected candidate and its validation error in the run notes would make failures like this debuggable in minutes. Today, only the final message survives.

## Tests repeated

- API contract: same ID for the same URL, same `runId` on a repeated refresh, 404s, and DELETE stops all requests at once (0 requests in the next 60 s, even mid-crawl). ✓
- Refresh costs $0 (0 model calls), and `lastChangedAt` is unchanged. ✓
- Layout break → `healing` (old data still served) → `serving` with the same IDs, on the hard fixture and the mock. ✓
- Captcha → `failed: blocked: captcha required`, data kept, no model spend. ✓

## Video advice (unchanged, one update)

Show the hard fixture (two-hop documents, numbered pager, local-time deadlines: all now correct), a repair, and the captcha. **Don't show UPOL live** until item 1 is fixed, and don't count achatpublic as a success.
