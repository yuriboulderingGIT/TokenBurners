# Watchtower: plan v2

::: {.subtitle}
Team TokenBurners · Patterno track · MAIN AI Hackathon, Maastricht, 20–21 Nov 2026 · status on Friday at 17:30, plan until the hand-in on Saturday at 10:30
:::

::: {.box}
**How to use this document.** This replaces the task lists and the timeline of plan v1. These parts of plan v1 still hold: the goal, the API contract and the tender fields (sections 1 and 3), the recipe format and the repo layout (section 4). Section 2 below lists what changed in the code since then. Everyone reads sections 1 to 3. Then go to your role in section 4. **Coding agents:** start with section 9, which tells you what to read and what you may change. All times are clock times.
:::

## 1. Where we stand (Friday 17:30)

We are far ahead of plan v1 on code and behind on proof. Almost every task from plan v1 is built and tested: 126 tests pass, CI is green, and the API matches the brief exactly (all 45 rules of the brief's appendix pass against a running server). **But no run with the real model is on record yet**: there are no measured costs and no real-portal results in the repo. Plan v1's checkpoint M2 (the LLM attaches to the mock and 3 real portals) is unproven. That is now our biggest risk, and the plan below starts with it.

| Checkpoint (plan v1) | Status |
|------------------------------------|-----------------------------------------------|
| **M1**: container builds; the API serves mock tenders with the hand-written recipe, no LLM | Reached by about 16:00 (planned for 19:30) |
| **M2**: the LLM attaches to the mock + 3 real portals, documents downloaded | Code done and tested with a fake model. Not yet shown with the real model |
| **M3**: mock layout change → healing → serving; captcha portal → failed | Passes with a fake model in tests; captcha → failed shown live. Not yet shown with the real model |

### Team Platform (A1 + A2)

| # | Task | Status | Evidence / what is left |
|---|----------------------|------------|-----------------------------------------------|
| P1 | API, exact contract | Done | 45/45 appendix rules pass live; `tests/contract_test.py` |
| P2 | SQLite tables, models | Done | plus an `llm_reservations` table for the model budget |
| P3 | Background worker | Done | refresh returns the running read or repair id; no request reaches a portal after DELETE |
| P4 | Fetcher, 1 request/s per portal | Done | limit is per portal, not per host (P14) |
| P5 | Dockerfile | Done | CI builds linux/amd64 and gets an answer on port 8080 in about 1 minute |
| P6 | State machine, reasons | Done | a crash during repair ends in `failed`, never a stuck `healing` |
| P7 | Document pipeline | Done | streaming, 250 MB cap, sha256, nested ZIPs, files reused per link; login-only documents → `failed` |
| P8 | Mock portal | Done | layouts v1/v2, captcha, JS app with JSON API, login-only documents, 300 MB ZIP |
| P9 | Health checks | Done | count drop, fill rate, changed ids, failing documents |
| P10 | Dashboard | Done | plus a page per portal (`/ui/portals/{id}`) and an add-portal form |
| P11 | README | Partly | cost per portal is not measured yet |
| P12 | Video | Not started | planned for Saturday morning |

### Team Brain (B1 + B2)

| # | Task | Status | Evidence / what is left |
|---|----------------------|------------|-----------------------------------------------|
| B1 | LLM wrapper, HTML shrinker | Done | cost log, cache, budget reservations. The 4,096-token output cap is untested with the real model (B10) |
| B2 | Real test set | Partly | 10 portals, 6 countries, 10 software packages. No portal whose documents need a login or captcha (B18) |
| B3 | Recipe executor | Done | runs the hand-written recipe on the mock |
| B4 | Attach, server-rendered portals | Partly | works with a fake model; never run with the real model (B10, B14) |
| B5 | Field cleanup | Partly | no time-zone fallback, so all deadlines can come back null (B13) |
| B6 | Attach, JS portals | Partly | works on a JS + JSON test page; no real JS portal yet (B14) |
| B7 | Repair loop | Partly | heals the mock layout change with a fake model. One dead tender page makes the whole portal `failed` (B11) |
| B8 | Login / captcha detection | Done | list pages, tender pages and documents |
| B9 | Multi-step downloads | Cut | as plan v1 allowed |

## 2. What changed since plan v1 (read before you code)

- **Documents:** `store_documents(db, portal_id, tender, links, fetch)` returns `StoredDocuments` (`documents`, `failed`, `blocked`, `block_kind`, `too_large`). A file is downloaded once per tender and link, then reused. Refreshes cost no extra requests and file ids stay the same.
- **Health:** `check_health(prev, new)` gets as `prev` only the tenders the last healthy read saw (`db.tenders_listed_last`), not older tenders kept as closed. `Health.missing_document_ids` tells repair which tenders lost their documents.
- **Read errors:** `run_recipe` raises `RecipeReadError` (HTTP 400/404/405/410/422, broken selectors) or `PaginationError` (incomplete page chain). Both count as a failed health check and start a repair. HTTP 5xx and network errors raise `httpx.HTTPError`: the last good tenders are kept and no repair starts.
- **Pages:** `MAX_PAGES` is 100. Recipe validation walks the whole page chain and samples 3 tenders.
- **Model budget:** every call first reserves its maximum cost (`llm_reservations`), then records the real cost. `BudgetExceeded` means the reservation did not fit. A model without a price entry in `brain/llm.py` cannot be used.
- **API details:** `.../tenders` and `.../tenders/` are the same portal. A refresh during a repair returns the repair (or re-read) run id. An open tender whose deadline has passed is served as `closed` at once, with `lastChangedAt` set to the deadline.
- **States:** failed portals get a plain re-read (no model) every `FAILED_RETRY_MINUTES` (default 180).
- **Dashboard:** HTML page per portal at `/ui/portals/{id}`. The JSON API paths did not change.

Unchanged from plan v1: the API contract and tender fields, the recipe format, the repo layout, the tech stack, and the rules (section 7).

## 3. Known problems on main (each one reproduced)

| # | Problem and effect | Where | Fixed by |
|---|-------------------------------------------------------|----------------|--------|
| 1 | One tender page answers 404 → the whole read fails → repair can't help → the portal goes to `failed`. A portal stops delivering because one tender was removed. | `brain/run_recipe.py` (tender page fetch) | B11 |
| 2 | A redirect from `http` to `https` or from `example.com` to `www.example.com` on the start address → "redirected outside the recipe's origin" → attach fails. Common on real portals. | `brain/run_recipe.py` `fetch_page` | B12 |
| 3 | A recipe without `locale.tz` → every deadline is null. The prompt says to give the time zone "only when the pages support them". The health check doesn't catch this on the first read. | `brain/normalize.py`, `brain/learn.py` | B13, B16 |
| 4 | Output cap of 4,096 tokens. GPT-5 models count hidden reasoning against it, so answers may be cut off ("could not attach: Expecting value …"). Not verified yet. | `brain/llm.py` | B10 |
| 5 | The HTML prompt doesn't name the optional field keys (`cpvCodes`, `placeOfPerformance.city`, …), so the model has to guess them and they are rarely filled. | `brain/learn.py` | B16 |
| 6 | Repair studies the first link of the list page (often a menu link) and doesn't check that tender ids stay the same. | `brain/repair.py` | B17 |
| 7 | Documents on another host (for example a download subdomain) are silently dropped. | `brain/run_recipe.py` | B19 |
| 8 | The rate limit is per portal, not per host. Only 4 portals are read at the same time. | `app/fetcher.py`, `app/config.py` | P14 |
| 9 | `.env.example` sets `DATA_DIR=./data`, so `docker run --env-file .env` writes outside the `/data` volume. | `.env.example` | P15 |
| 10 | A portal with more than 100 list pages can't attach. | `brain/run_recipe.py` | accepted for now |
| 11 | The brief says the LLM should also *check* a portal. We only use it to learn and to repair. | `brain/` | B20 (optional) |

## 4. Who does what next

Same two teams as plan v1. New task numbers continue plan v1's (P13 and up, B10 and up). Some Brain tasks move to A2 to balance the load (marked "moved"). Times are estimates. "Done when" is the test.

### Team Platform (A1 + A2)

| # | Task | Owner | Time | Done when |
|---|---------------------------------------------------------|------|-----|--------------------------|
| P13 | **Hand-in key, first.** Check that the credits are API credits. Make one key in its own OpenAI project with a usage limit. Set `LLM_BUDGET_USD` to that limit. Give it to B1 for B10. | A2 | 0.5h | B10 runs on the hand-in key; limit and variable in the README |
| P14 | `WORKER_CONCURRENCY` default 16. Rate limiter keyed by host, so portals on one host share it. | A1 | 1h | Test: two portals on one host are never less than 1 s apart |
| P15 | `.env.example` without `DATA_DIR`. README run line keeps `-v watchtower-data:/data`. | A1 | 0.25h | `docker run --env-file .env` keeps data in `/data` |
| P16 | Mock switches for the known problems: one dead tender page (404), list page moved, `http` → `https` redirect. | A2 | 1h | B11 and B12 have a failing test on the mock before their fix |
| P17 | Real-portal runner: `testset/run_all.py` posts every test-set portal to a running container, waits for a final state, writes `testset/results.md` (state, reason, tenders, documents, model cost). | A1 | 1h | One command gives the results table for B14 and the overnight run |
| P18 | CI: after `docker build`, start the container next to the mock and check the API contract against it. | A1 | 1h | CI fails if the container's API shape breaks |
| P19 | Tune health checks on real reads: run them on the 10 real portals, fix false alarms (date-only deadlines, small portals). | A2 | 1h | No correctly read real portal trips a check |
| P20 | Overnight run: at 23:30 start the built container with the hand-in key on all test-set portals and the mock, `REFRESH_MINUTES=15`, laptop kept awake. | A1 | 0.5h | In the morning the dashboard shows a night of reads |
| P11 | README (from plan v1): one page, cost per attach, repair and read measured from `llm_calls` (section 6). | A2 | 1h | Numbers from the overnight run are in |
| P12 | Video (from plan v1), 3 minutes max: add portals → serving; tenders and documents; layout change → healing → serving; captcha → failed; morning dashboard; costs. | A1 | 1.5h | Uploaded by 09:00 |

### Team Brain (B1 + B2)

| # | Task | Owner | Time | Done when |
|---|---------------------------------------------------------|------|-----|--------------------------|
| B10 | **Real-model attach, first.** Hand-in key, mock (server-rendered, JS app, documents) plus 3 real server-rendered portals. If answers are cut off ("Expecting value"), raise `MAX_OUTPUT_TOKENS` to about 16k and use low reasoning effort. | B1 | 1.5h | Mock and 3 real portals serving with documents; cost per attach written down |
| B11 | A tender page that answers 404 or 410 is skipped and counted; the read goes on. The health check fails only when many are skipped. | B2 | 1h | P16 dead-page switch: the portal stays serving with 4 tenders |
| B12 | Follow `http` → `https` and `www` redirects of the start address before learning, and use the final origin in the recipe (with B1 for `learn.py`). | B2 | 1h | P16 redirect switch attaches |
| B13 | Time-zone fallback: no `locale.tz` → take it from `locale.country`, else from the domain (`.de` → Europe/Berlin, `.fr` → Europe/Paris, …). | A2 (moved) | 0.5h | Unit tests for 6 countries; a recipe without tz still gives deadlines with an offset |
| B14 | Real test-set run with P17's runner: attach all 10 portals, sort the failures by cause, fix the 3 biggest causes. | B1 + B2 | 3h | `results.md`: every portal serving, or failed with an honest reason |
| B15 | Real-model repair on the mock: layout v2 → healing → serving; captcha → failed. Write down the repair cost. | B1 | 1h | M3 shown with the real model |
| B16 | Prompt: name every field key (`referenceNumber`, `description`, `procedureType`, `contractNature`, `cpvCodes`, `status`, `url`, `placeOfPerformance.postalCode` / `.city` / `.countryCode`) and always ask for an IANA time zone. | B1 | 0.5h | Real portals fill optional fields where they show them |
| B17 | Repair studies a stored tender's page, not the first link. Reject a repaired recipe whose sample ids don't match the stored ids. | B1 | 1h | Test: a layout change keeps the same tender ids |
| B18 | Test set: add 1–2 portals whose documents need a login or captcha (check Sell2Wales). Move 3 portals into `testset/secret.yaml`. Save fixtures. | A2 (moved) | 1h | A blocked case in `portals.yaml`; 3 secret portals exist |
| B19 | Allow document links on other hosts (still rate limited). | B2 | 1h | A document from another host is delivered |
| B20 | Optional: after attach or repair, the cheap model compares 2 extracted tenders with their pages (purpose `check`). A mismatch is a health problem. | B1 | 1.5h | Cut first if we are behind |

**Total:** about 20 person-hours of work, with about 18 available before the feature stop (4 people × 4.5 hours). B19 and B20 happen only if there is time.

## 5. Timeline and checkpoints (clock times)

| Phase | Time | Team Platform | Team Brain | Checkpoint |
|------|----------|-------------------------|-------------------------|-------------------------|
| 1 · Make it real | 17:30–19:00 | P13 first, P14, P15, P16, start P17. A2: B13 | B10 first, B11, B12 | **M2r (19:00):** with the hand-in key, the container attaches the mock + 3 real portals, documents downloaded, cost per attach known |
| 2 · Real portals | 19:00–22:00 | P17, P18, P19. A2: B18 | B14, B15, B16, B17. B19, B20 if time | **M3r (22:00):** all 10 test-set portals tried, honest states in `results.md`; the mock layout change heals with the real model |
| 3 · Freeze | 22:00–23:30 | **Feature stop at 22:00.** Fixes only, each with a test. `docker build` on a clean machine. P20 at 23:30 | Run the 3 secret portals on the built container. Fix only crashes and dishonest states | Overnight run started |
| Night | 23:30–07:30 | Sleep | Sleep | |
| 4 · Ship | 07:30–10:30 | P12 video by 09:00, P11 final numbers | Read the overnight results. Tiny fixes only | **Submitted by 09:30**, one hour spare |

### If we fall behind, cut in this order

1. B20 LLM check
2. B19 documents on other hosts
3. P18 CI check against the container
4. P14 per-host rate limit (still raise the concurrency to 16)
5. B16 optional field names

::: {.warn}
**Never cut:** B10 (real-model attach), B11–B13 (the reading bugs), P11 (README with measured cost), P12 (video), honest states, and `docker build` on a clean machine.
:::

## 6. Model cost and credits

- **The judges' run is billed to our key.** P13 comes first: one hand-in key in its own OpenAI project with a usage limit. Set `LLM_BUDGET_USD` to that limit minus a margin. It is the total for the whole data folder, shared by all portals.
- **Models:** `gpt-5-mini` for attach, `gpt-5` for repair (or after the cheap model failed twice). Another model needs a price entry in `brain/llm.py`.
- **Calls:** a regular read makes no model call. An attach makes up to 4 cheap calls and 1 strong call. A repair makes up to 3 strong calls.
- **Plan v1's target still holds:** if one attach costs more than about $0.10–0.20, shrink the HTML more before scaling up.
- **Measure it (P11):** run the container with `-v "$PWD/data:/data"`, then on your laptop:

```
sqlite3 data/watchtower.db "SELECT purpose, COUNT(DISTINCT portal_id) AS portals,
  ROUND(SUM(cost_usd), 4) AS usd, ROUND(SUM(cost_usd) / COUNT(DISTINCT portal_id), 4) AS usd_per_portal
  FROM llm_calls GROUP BY purpose;"
```

- **Coding agents** run on subscriptions (Claude, Codex through a ChatGPT login), never on the hand-in key.

## 7. Rules we stick to

- **Honest states earn points.** Never `serving` with stale or bad data. When unsure, `failed` with a reason in plain words.
- **Ids come from the portal and never change.** Tenders that leave the list are kept and marked `closed`.
- **All HTTP goes through `app/fetcher.py`. All model calls go through `brain/llm.py`.** (Checked at 17:00: no exceptions in the code.)
- **The API JSON is frozen.** `tests/contract_test.py` passes before every merge to main.
- **Every fix comes with a test** (a mock switch or `FakeFetcher`).
- **Regular reads never call the model.**
- **Feature stop at 22:00.** After that: fixes only, each with a test.
- **Develop on fixtures and the mock.** Real portals: at most 1 request per second, no logins, no captcha solving.
- **After 09:00 on Saturday,** a merge needs a second person's OK.
- **Changes to `app/models.py` or the recipe format** are announced to the whole team first.

## 8. Hand-in checklist (status at 17:30)

| ✓ | Item | Status |
|---|--------------------------------------------------------|--------------------------|
| ☐ | Git repo linked in the hackathon submission, Dockerfile at the root | Dockerfile done; link at submission |
| ☑ | `docker build .` works on linux/amd64 in reasonable time | CI, about 1 minute |
| ☑ | The default command serves the API and dashboard on port 8080 | CI checks it |
| ☐ | Contract checks pass against the built container | P18 |
| ☐ | One-page README: how it works, environment variables, measured cost per portal | P11 |
| ☐ | Hand-in API key funded and limited; values given at hand-in | P13 |
| ☐ | Video of at most 3 minutes, including the dashboard | P12 |
| ☐ | Tested on the 3 secret portals; failures show an honest reason | Phase 3 |
| ☐ | General MAIN submission page checked for extra items (slides, form fields) | Saturday morning |

## 9. Briefing for coding agents

Paste the shared block into your agent first, then add your role's block. Agents should read section 2 of this document and the files they own before writing any code.

### Shared block (every agent)

```
You are working on "Watchtower", a 24-hour hackathon project for Patterno (MAIN AI Hackathon).
It is ONE Docker container serving a FastAPI API and an HTML dashboard on port 8080. Given only a
public-tender portal URL, it learns a "recipe" with an LLM (once), reads tenders with plain code
(no LLM), checks health after every read, and repairs the recipe with an LLM when a portal
changes. Nobody may steer it while it runs.

State on Friday at 17:30: everything is built and tested with fake models (126 tests, CI green),
and the API matches the brief exactly. What is left: make it work with the real model on real
portals, fix the known problems, hand in. Read sections 2 and 3 of "Watchtower plan v2" first.

Hard rules:
- Do not change API paths, status codes or JSON field names. tests/contract_test.py must pass.
- All HTTP goes through app/fetcher.py (max 1 request/second per portal, documents included).
- All model calls go through brain/llm.py (cost logging, cache, budget reservations).
- Regular reads never call the model. A wrong value is worse than a missing one: output null
  when unsure.
- Times are ISO 8601 with offset. CPV codes without check digit. No logins, no captcha solving.
- Every fix needs a test (mock_portal switches or FakeFetcher). Run pytest before every push.
- Only edit the files your role owns. Ask before changing app/models.py or the recipe format.
- Feature stop at 22:00: after that, fixes only.
```

### Role blocks

| Role | Owns | Paste this goal |
|------|-----------------------|-----------------------------------------------------------|
| A1 Platform | `app/main.py`, `db.py`, `models.py`, `worker.py`, `fetcher.py`, `config.py`, `templates/`, `Dockerfile`, `.github/`, `tests/contract_test.py`, `testset/run_all.py` | Do P14 (`WORKER_CONCURRENCY` default 16; rate limiter keyed by host, with a test), P15 (`.env.example` without `DATA_DIR`) and P17 (`testset/run_all.py`: post every portal in `testset/portals.yaml` to a running container, wait for a final state, write `testset/results.md` with state, reason, tenders, documents and model cost). Then P18 (CI starts the built container next to the mock portal and checks the API contract against it). Tonight P20, tomorrow P12. |
| A2 Quality | `app/states.py`, `health.py`, `documents.py`, `mock_portal/`, `README.md`; moved to A2: `brain/normalize.py`, `testset/` (except `run_all.py`) | Do P13 (hand-in key), P16 (mock switches: one tender page answers 404, list page moved, `http` → `https` redirect, each with a test that fails today), B13 (time-zone fallback in normalize: from `locale.country`, else from the domain's country code) and B18 (1–2 real portals whose documents need a login or captcha; 3 portals into `secret.yaml`). Then P19 (tune health checks on real reads) and P11 (README with measured costs). |
| B1 Attach + repair | `brain/llm.py`, `shrink.py`, `learn.py`, `repair.py`, `blocks.py` | Do B10 first (real attach with the hand-in key on the mock and 3 real portals; if answers are cut off, raise `MAX_OUTPUT_TOKENS` to about 16k and use low reasoning effort). Then B15 (real-model repair on the mock layout change; captcha → failed), B16 (name every field key in the HTML prompt; always ask for an IANA time zone) and B17 (repair studies a stored tender page; reject recipes whose sample ids don't match the stored ids). With B2: B14. Optional: B20. |
| B2 Data + JS | `brain/run_recipe.py`, `js_discover.py` | Do B11 (a tender page that answers 404 or 410 is skipped and counted; the read goes on) and B12 (follow `http` → `https` and `www` redirects of the start address and use the final origin; agree the `learn.py` part with B1). Then B14 with B1: run all 10 test-set portals with `testset/run_all.py` and fix the 3 biggest failure causes. If time: B19 (document links on other hosts). |
