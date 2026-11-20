# Watchtower Plan v3

**TokenBurners · Patterno track · Checkpoint at 23:14 CET, 20 November 2026**  
Release plan and technical handoff for agents working on Watchtower.

## 1. Release decision

**Prioritize accurate delivery and honest status.** Prove an unseen portal from a URL through stored tenders and downloadable files, then show a change breaking delivery and a repair restoring it. Keep the last correct snapshot available throughout repair. Fix the confirmed causes of false success before increasing the number of portals.

**Submissions close Saturday, 21 November at 11:00 CET (GMT+1).** Internal hand-in stays at **10:30 CET**. Team rules allow fixes only after Friday 22:00 and require a second reviewer from Saturday **09:00 CET**.

| Checkpoint signal | Verified at this checkpoint |
| --- | --- |
| Candidate base | Local and remote main at 2760a5e. Clean before this plan. GitHub tests and Linux amd64 container contract passed. |
| Current code | 232 standard tests passed. Separate ingestion probes produced 8 failures and 3 passes. All 4 offline proof scenarios passed. |
| Local dashboard data | 6 of 18 active entries report serving. Only 2 of those expose any files in their published tender records. Coverage and fresh attach remain unproven. |
| Release risk | Incomplete output can appear healthy. Anonymous browser requests can lose session state or a required response. Model-assisted checking remains a track gap. |

Each grading criterion is scored 1–5 and carries **20%**.

| Criterion | Evidence to show the jury | Current assessment |
| --- | --- | --- |
| Attach | Add a withheld URL from clean storage. Match every open notice to source IDs, permalinks and downloadable files. | Mock SSR and JSON proven offline. Current public proof is partial or historical. |
| Notice | Correctly distinguish healthy, incomplete, blocked and stale data. Show the reason and preserve the prior good snapshot. | CAPTCHA fixture works. Duplicate rows and preview listings can pass incorrectly. |
| Repair | Change a fixture after attach. Show failed read, healing, repair, then correct IDs and files again. | Offline proof passes. Repeat with the real model at the release SHA if budget allows. |
| Dashboard | At a glance, see delivery, last good data, the current run, what broke and what happened next. | Counts and history exist. File coverage and stale or incomplete delivery need clearer treatment. |
| Approach | Show request counts and latency, fallbacks, success and failure costs, and zero paid calls on ordinary reads. | Ledger and limits exist. Historical costs are measured. Large traversals need work. |

The track brief and grading screenshot are reference material for this checkpoint. The jury will run unseen private fixtures and real portals. Once a URL is supplied, no person or team service may steer the run.

<!-- pagebreak -->

## 2. Recent merges and verification

Reviewed merges since the v2 checkpoint through **2760a5ea68e50620030e9d08a161445189e5adcb**. The three alternative designs remain on their own branches and in the sibling design-variants folder. Keep the main dashboard for hand-in.

| Integration | What landed | What remains |
| --- | --- | --- |
| [#11 · b01d05b](https://github.com/yuriboulderingGIT/TokenBurners/pull/11) | Updated team workflow and file ownership. | Apply the v3 priorities within those owners. |
| [#12 · 1bd8085](https://github.com/yuriboulderingGIT/TokenBurners/pull/12) | Linux container contract, platform and quality fixes, and public test harness. | Live evidence needs a fresh, source-checked run. |
| [#13 · 47761d1](https://github.com/yuriboulderingGIT/TokenBurners/pull/13) | Recover notice links when the listing model misses an unambiguous permalink. | Several live recipes still publish shared listing URLs. |
| [#14 · 193d058](https://github.com/yuriboulderingGIT/TokenBurners/pull/14) and 2acc882 | Better gate and pagination diagnosis. Prevent recurring paid attaches on failed URLs with no recipe. | A genuinely transient first request has no bounded scheduled retry. |
| [#15 · 7156e07](https://github.com/yuriboulderingGIT/TokenBurners/pull/15) | First-read progress, labelled pagination, data-oid detection and defensive optional XPath. | Progress and a plausible first sample do not establish complete delivery. |
| 99ea430 and a0d3800, integrated via 69ae242 | Workflow and independent setup PDFs. | Use current README defaults and the new evidence below. |
| [#16 · 4507db1](https://github.com/yuriboulderingGIT/TokenBurners/pull/16) | Intermediate listing discovery and bounded observed anonymous POST search. Supports totals and HTML fragments in JSON. | The browser still loses cookies and headers and cannot consume certain replayed responses. |
| [#17 · 3dc293e](https://github.com/yuriboulderingGIT/TokenBurners/pull/17) | Blank GET search, Berlin pagination, atomic verified repair with HTML-to-JSON fallback, Berlin and Charité document handlers, dashboard counts. | Coverage varies by listing and file class. Special handlers need source-level verification. |
| [#18 · d213681](https://github.com/yuriboulderingGIT/TokenBurners/pull/18) | Offset and nested GraphQL hints, CDN script support and targeted anonymous cross-origin reads. | Observed GET data on an API subdomain is still unsupported. |
| [#19 · 6020c14](https://github.com/yuriboulderingGIT/TokenBurners/pull/19) | Raised the browser script response cap from 4 MiB to 8 MiB. | This addresses script size. The session and replay findings still reproduce. |
| 2760a5e | Added the site-reading audit and offline probes. | Audit-only commit. Fixes below are still open. |

**Conflict resolutions.** The bug-hunt merges preserved the 16,384-token model cap, fixed the repair call signature, and retained the first-read retry controls and default concurrency of 16. The #18 merge reconciled overlapping collection changes in brain/run_recipe.py. Both historical live reports were preserved.

**Fresh checks.** Standard suite **232 passed** with browser cases available. Targeted audit **8 failed, 3 passed** on the same SHA. Seven fail for defects. One exercises the current API-subdomain restriction. The requested **side_read_reproduction.md** was absent from the checkout and Git history. The [audit](output/site-reading-audit.md) identifies [site_read_reproductions.py](output/site_read_reproductions.py) as its companion, which was read and rerun.

**Offline proof.** SSR and SPA each delivered **5 tenders and 8 files**. A layout change triggered a repair that restored those same outputs. The CAPTCHA case moved to failed and retained **5 and 8**. These use fixtures and no paid model calls. The clean [GitHub CI run](https://github.com/yuriboulderingGIT/TokenBurners/actions/runs/44825707013) also built Linux amd64 and exercised the image’s container contract. This machine did not build Docker locally.

<!-- pagebreak -->

## 3. Live dashboard delivery

Read-only SQLite snapshot at **23:14 CET**, covering earlier runs and seeded recipes. Counts reflect **published tender records**, including retained closed notices. Code tests in section 2 were independent. Stored file metadata can include files missing from the published tender record.

| Reported serving | IDs / files | What the data shows |
| --- | --- | --- |
| Charité | 33 / 109 | Files across 24 notices, including public Q&A and revised files. Main tender packages require acknowledgment. All 33 notice URLs share one address in the snapshot. |
| Berlin filtered | 1 / 38 | Public Berlin/RIB files on one tender using a seeded filtered recipe. Below the five-notice positive target. |
| Berlin full | 249 / 0 | 243 deadlines, only 25 distinct URLs. Latest read still running after 84 minutes. Last success at 21:36 CET. Investigate before repeating. |
| Hamburg | 23 / 0 | 21 open, with 23 deadlines and distinct links. Seeded recipe and no verified file delivery. |
| Vergabekooperation Berlin | 10 / 0 | All 10 point to the same page. Check list completeness beyond the homepage preview and find canonical details. |
| Patterno individual notice | 138 / 0 | Registration was for one notice, but the recipe switched to the broader Patterno listing. No deadlines. Verify scope and source before counting it. |

All **12 failed** entries had zero published tenders and files. The table records observed symptoms. A site may have more than one cause.

| Observed failure | Sites | Next evidence |
| --- | --- | --- |
| JS shell or empty discovery | Deutsche Bahn, TED, Vivantes | Look for an observed public response or correct buyer listing. TED also reported invalid XPath. |
| Pagination bound | DTVP, Lower Saxony | Check advertised totals and actual termination. If deliberately scoped, use a shareable source filter and label the scope. |
| Missing source ID or title | Brandenburg, Autobahn, BWI | Reach the real results and notice pages. Compare stable source IDs before considering a repair valid. |
| Redirect, link or entry mismatch | RIB FU Berlin, NRW, Baden-Württemberg | Check canonical entry points and verified public handoffs. Baden-Württemberg’s root has a redirect pattern. |
| Selectors and document workflow | Cyprus | An earlier run found 10 notices and 4 files, but shared one listing URL. The current attempt failed. Reproduce detail and document selection before retrying. |

**Evidence to present.** Six sampled files from local storage matched the published byte sizes and hashes, three each from Berlin filtered and Charité. Use the mock for full attach–notice–repair. Show failed and incomplete cases in the dashboard. A fresh public attach remains the main validation gap.

<!-- pagebreak -->

## 4. Fix before the release checkpoint

### P0-A · Deliver the right notices and report the right state

**A2 Quality leads** in app/health.py, app/documents.py and brain/normalize.py. **A1 Platform** owns commit and display boundaries in app/worker.py, app/db.py and templates. **B1/B2** own learning and extraction if source IDs or links are lost upstream.

- Reject conflicting duplicate source IDs on the full read before publishing or closing any tender. Deduplicate identical overlap explicitly. Distinguish full enumeration, known filtered scope, an incomplete traversal and a verified empty result. Add a separate blocked-file reason when a required public delivery path is unavailable.
- Verify canonical tender URLs when the source exposes them. Compare link scope, list totals and pagination signals. Include the zero-file case: if the source advertises accessible documents and bytes are missing, report incomplete delivery. Surface known gates as failed with a cause. Treat a truly document-free listing as such only when supported by source evidence.
- Keep one atomic publish of tenders, files and recipe after verification. While healing or when a run fails, label the retained snapshot, its last success time and the active or failed attempt separately. Use the existing API states and reason field. Put phase, counts, scope and recent action on the dashboard.
- **Acceptance.** The late-collision probe passes. A partial or repeated page cannot close omitted notices. Source and stored IDs, URLs, deadlines and file hashes match the fixture, including two rows with a conflict and a confirmed empty listing. A blocked or partial read is visibly different from a healthy document-free read.

### P0-B · Isolate portal sessions and finish anonymous browser reads

**A1** owns app/fetcher.py. **B2 Data/JS** owns brain/js_discover.py; coordinate brain/public_search.py with its owner. Land session isolation and browser replay with tests that exercise the same request path as discovery.

- Separate cookies per monitored portal while preserving shared host and per-portal throttling. Propagate anonymous browser and HTTP session state within one visit. Respect browser cookie rules in emulated responses.
- Preserve the minimal observed read headers that affect content negotiation or anonymous session checks. Return accepted read-only POST responses to the renderer so a subsequent fetch can run. Retain destination and redirect checks, keep transient session values in memory, and constrain replay to observed anonymous reads.
- **Acceptance.** The cookie isolation, browser session cookie, JSON Accept header and POST response probes pass. Existing normal and CDN browser tests stay green. Regression fixtures cover unrelated destinations, a disallowed write and absence of persisted session secrets. Measure rate spacing for two portals on one host.

### P0-C · Protect identity and published bytes through repair

**B1 Attach/Repair** owns brain/learn.py and brain/repair.py. **A1/A2** verify the publish and health boundaries from P0-A. Keep the recent HTML-to-JSON repair path and last-good behavior through an incompatible layout change.

- **Acceptance.** First read detects the break, a separate repair run starts, and the old API snapshot remains accessible during healing. The replacement must restore source IDs, notice URLs and comparable document coverage. A failed candidate leaves the old recipe and data intact. Ordinary subsequent reads have zero model calls.

<!-- pagebreak -->

## 5. Bounded follow-up fixes

Start these only where P0 is covered or the work directly unblocks its evidence. Give each change a fixture or a before-and-after measurement. Keep new recipe transport and broad portal coverage work behind the release gate.

| Work | Owner and paths | Required result |
| --- | --- | --- |
| P1-D · Initial transient retry | A1 app/worker.py and B1 brain/learn.py | Carry pre-model 503 and temporary network errors into an explicit, bounded retry with backoff. No new paid learning loop after bad candidates, access gates or exhausted budget. The initial 503 probe passes and the existing no-recipe guard still passes. |
| P1-E · Resolve effective base | B2 brain/run_recipe.py and B1 brain/learn.py | Honor the effective HTML base in learning and read-time detail, pagination and document extraction. Keep allowed destination checks. The base-URL probe passes. |
| P1-F · Reuse one attach traversal | B1/B2 with A1 worker integration | Reuse verified listing batches and the anonymous visit within one attach attempt. The 50-page probe currently makes 100 listing GETs, taking at least 108.9 seconds at 1.1-second spacing before details. Target one complete walk and report the actual count. Later refreshes fetch fresh data. |
| P1-G · Model-assisted checking | B1 with A2 health and A1 worker | The track asks for an LLM to learn, check and repair. Current explicit checks are deterministic. If safe to land, use a tightly budgeted check on ambiguous attach/repair evidence, log it separately, and keep structural checks authoritative. Ordinary reads stay model-free. Disclose the gap if deferred. |
| P1-H · Verified handoffs only | B2/A2 | Consider a specifically observed anonymous GET API subdomain or public file handoff only if it helps a selected validation case. Retain destination, redirect and cookie constraints. Treat the existing subdomain probe as a scope limit until supported. |
| P2 · Better targets and docs | A2 testset and README | Replace the unfiltered BOAMP test input with a bounded source query and valid offsets. The fixture advertises about 1.73M rows with limit=3 and no offset. Document actual query scope, keep the page limit, and fix README’s LLM_CACHE default to 0. |

**Search behavior to document and test.** A blank search may enumerate the full results through a public GET form or an observed read-only workflow. Follow real pagination and reconcile its advertised total where available. If the portal requires terms, use a supplied shareable result URL or an explicit public source filter. Label it as scoped. A form with no verified enumerable path should get an actionable reason. Never treat guessed terms or a homepage preview as the whole portal.

**Document priorities.** Validate direct links, an anonymous supported multistep path and ZIP extraction. For Charité, keep delivered Q&A or revised files distinct from the main package; demonstrate an anonymous permitted package flow or display its block. Preserve per-portal spacing of at least one second, with a default interval of 1.1 seconds. Do not bypass login or CAPTCHA. Defer broadening a site-specific handler or raising the 100-page cap without completeness and resource evidence.

<!-- pagebreak -->

## 6. Release candidate validation

### Run on the exact candidate SHA

Use Python 3.12 and the project requirements. Install Playwright Chromium for the standard browser tests. The current audit script uses Microsoft Edge. On Windows start one uvicorn worker with reload off. Use disposable fixture storage for repeatability and preserve the development database and spending ledger.

~~~sh
python -m pytest -q -p no:cacheprovider
python -m pytest output/site_read_reproductions.py -q -p no:cacheprovider
python -m brain.proof --rehearse
docker build --platform linux/amd64 -t watchtower .
bash tests/container_contract.sh watchtower
~~~

The probe command is intentionally red on the baseline. Check that each fixed defect turns green and carry confirmed regressions into the standard suite. Record any support-limit probes that still fail. CI is the Linux build and contract check if Docker is unavailable locally.

### Give the jury a test they can repeat

1. **URL-only attach.** Start the image on 8080 with fresh data, no seeded recipes and only documented private env values. Add one withheld fixture and one feasible real portal. Match every open source ID, detail URL, title, buyer, date and offset-bearing deadline where shown. Record source file names, API paths, byte sizes and SHA-256. Sample browser checks are useful before a run. The final claim needs collection-level evidence.
2. **Read, break, and heal.** Compare an unchanged refresh, a broken layout and a recovered read. Check run and state transitions, last-good data during healing, source-equivalent data afterwards, and no changes in lastChangedAt for unchanged records. For active login/CAPTCHA blocks, require failed with a clear reason and validate retained data.
3. **API under repetition.** POST returns 201 immediately and the same URL yields the same ID. Refresh returns 202 and the same active runId. Runs expose read/repair and an in-flight null result. changedSince is strictly later. Unknown IDs return 404. DELETE returns 204 and requests stop within a minute. Check ZIP unpacking, the 250 MB allowance, null required fields and omitted unknown optional fields.
4. **Politeness and spend.** One bounded attempt at a time on real portals. Respect the host and portal request interval and back off on throttling. Record URL scope, SHA, clean vs seeded, request count, run IDs, duration, open tender count, downloadable files and coverage, plus model calls and cost by phase and outcome. With budget owner approval, use python -m brain.proof --live --only mock for real-model mock proof and a single targeted public attempt.

### Select targets by evidence

Keep the SSR, SPA, layout-change and block fixtures. Use Masaryk or Cyprus as candidates only after independently checking **five open tenders with public files**; the prior Masaryk run had 15 notices and the earlier Cyprus run had four files. Use Berlin filtered for a short file demo and Charité for partial delivery. For the broader matrix, log verified candidates across **4+ countries**, **3+ software vendors**, SSR, JavaScript and multistep flows, and **1–2 blocked** cases. Keep a few held out for final testing. The existing testset has geographic breadth, but its labels do not establish current public document coverage.

<!-- pagebreak -->

## 7. Schedule, evidence and submission

| CET · 21 Nov | Owner | Exit condition |
| --- | --- | --- |
| Before 07:30 | A1/A2/B1/B2 | Land small P0 fixes with tests. Pair-review file overlaps. Save a candidate or roll back the change that broke the contract. |
| 07:30–08:30 | A2 + A1 | Capture dashboard, fixture matrix and clean image verification. Run one qualified live candidate if the budget owner approves. Replace draft counts with measured data. |
| 08:30–09:00 | Release lead + video owner | Lock the release SHA and finish the video cut. From 09:00, require a second reviewer for any merge and repeat affected checks. |
| 09:00–10:30 | Release lead | Verify video, repo link, default container, README and private env handoff. Submit by the internal target. |
| 11:00 | Organizer cutoff | Submissions close. The team’s 10:30 target leaves 30 minutes. |

**Cost numbers already on record.** Earlier real-model mock attach **$0.005485** for 5 tenders / 8 files, repair **$0.028319** for the same outputs, and ordinary refresh **$0**. Earlier Cyprus attach **$0.019190** for 10 tenders / 4 files with a permalink limitation. That 10-portal public study reached one serving; failed attach runs averaged **$0.031349**. A different B14 run reached zero of ten and recorded **$0.66145**. The local development snapshot has **88 attach calls / $0.828649** and a separate outstanding **$0.038950** reservation. These are different data folders or test cohorts; keep them labelled. Measure successful and unsuccessful cases from the chosen SHA if time permits.

**Handoff checklist.**

- Attach the repository with a root Dockerfile. The default container must run API and dashboard together on port 8080 with Linux amd64. Verify a clean build and the exact run command in the README.
- Update README’s one-page architecture, env defaults, limits and measured cost. Supply the judging key privately. Keep .env, data/, private URLs and session material out of Git, screenshots and video.
- Record the dashboard showing URL-only attach, a tender and a file, healing and recovery, and a truthful blocked state. The track caps the video at **3:00**. The existing pitch brief targets **2:30 or less**. Replace [FILL] figures and unsupported claims in the script. Source folders contained a brief and script; no final video was verified.
- Keep the main dashboard. If a fresh live case or model-assisted check misses the release gate, show the passing fixture proof, label partial public runs, and state the remaining gap. Keep the last candidate with a green container contract.

**Agent handoff.** Open one small PR per fix, with owner, SHA, repro result, tests and effect on delivery. Coordinate shared files under [DEVELOPING.md](DEVELOPING.md). Flag state or recipe format changes to all roles. Preserve existing recipes, requests, data and spending ledgers when validating fixes.

**Source record.** Patterno Track Description pp. 1–3 and MAIN grading screenshot (user attachments). Repo references include [Plan v2](Watchtower_Plan_v2.md), [README](README.md), [audit](output/site-reading-audit.md), [proof harness](brain/proof.py), [testset](testset/portals.yaml), [live results](testset/results.md) and [B14 results](testset/b14-results.md).
