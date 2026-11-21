# Portal delivery verification · 21 November 2026

Four public buyer or tender URLs were fixed before the first run. The hosts were absent from the tracked portal lists and the development portal registry. Each registration used the normal API, an empty delivery database, the production fetcher, a ten-minute limit, and the shared model ledger. A serving status alone earns no pass. Transfer totals include redirects and browser resources. Source bodies and files stay in the ignored trial directory.

## First attempt on each public portal

| Entry | State at finish | Tenders / files published | Seconds | Requests | Bytes | Model USD |
|---|---|---:|---:|---:|---:|---:|
| [FGC, Spain · PSCP](https://contractaciopublica.cat/ca/perfils-contractant/detall/FGC) | Failed. Chromium loaded the buyer profile and counters. Opening the buyer’s current-notices tab was necessary to reach the listing feed. | 0 / 0 | 54.45 | 38 | 1,539,110 | 0.000513 |
| [Charles University, Czechia · E-ZAK](https://zakazky.cuni.cz/) | Integrating at timeout. The reader scanned the mixed archive and was still processing documents for 3,400 listing entries. | 0 / 0 | 600.05 | 334 | 251,315,540 | 0.010547 |
| [Roma Capitale, Italy · TUTTOGARE](https://gare.comune.roma.it/) | Failed to connect on three attempts. No portal response reached the reader. | 0 / 0 | 69.31 | 3 | 0 | 0 |
| [Public Contracts Scotland, UK](https://www.publiccontractsscotland.gov.uk/Search/Search_MainPage.aspx) | TLS certificate-chain validation failed before any HTML arrived. A separate verified system-trust check reproduced the error. | 0 / 0 | 6.31 | 3 | 0 | 0 |

**Blind result: 0/4 complete, zero false serving states.** The release target is 3/4 complete. Scotland also requires supplier registration to obtain attachments, according to its [supplier FAQ](https://www.publiccontractsscotland.gov.uk/sitehelp/help_faq.aspx). The connection results describe this runner.

## Controlled and known-portal evidence

- **French judge fixture.** Two clean model-backed runs passed all 99 checks for 11 notices and 44 files. Between them, a clean run rejected an incorrect listing ID in 81 seconds. The reader now discards the conflicting archive shortcut and validates the official notice route independently. The successful rerun took 109.61 seconds, 58 requests, 26,061 bytes and $0.006688. The earlier success cost $0.010758 and the failed attempt cost $0.010609.
- **Language coverage.** Greek, Japanese and Arabic regression fixtures exercise the normal registration and file API with recorded model answers. They check source-language status mappings, localized times, IDs, buyer text, notice URLs and exact file bytes. Immediate refreshes rotate the document access check with zero model calls. A subsequent login wall preserves the previous snapshot and file bytes.
- **UPOL, known Czech portal.** A zero-model live replay used recorded selectors and the portal’s observed aggregate active-status filter. It published after 146.22 seconds, 114 requests and 161,692,013 bytes. All five open notices on the unfiltered second page were present, including P26V00000041 (24 November) and P26V00000281 (7 December). The first delivery exposed a split ZIP problem. Replaying the captured responses after fixing the final-volume handling delivered **18 notices and 104 documents from 88 source downloads**. An independent comparison verified IDs, titles, buyers, per-notice attachment association and all 104 hashes. Every file also passed the file API check. The corrected replay took 5.77 seconds offline. [UPOL archive page two](https://zakazky.upol.cz/contract_index.html?page=2)
- **achatpublic.** An inspected real notice placed Q&A attachments outside its primary tender dossier. The reader now requires the selected dossier panel on tabbed notices and can report the missing public panel. The saved real page exercised this check. This was a notice-level inspection. [Official notice](https://www.achatpublic.com/sdm/ent/gen/ent_detail.do?PCSLID=CSL_2026_RCHwC1j9QS)
- **Repair.** The existing offline browser rehearsal delivered 5 tenders and 8 documents on SSR and JavaScript layouts, recovered the changed layout, and reported the CAPTCHA gate.

## Post-fix public retests

These URLs were already known when the fixes were made. They cannot change the blind score.

- **Charles University.** The post-fix run selected the aggregate active view and published 233 rows, including 21 classified open, and 615 files in 333.53 seconds. It used 184 requests, 292,004,948 bytes and $0.018154. Every published file matched its API hash. Source inspection found missing buyer names and two open rows without documents. This delivery does not meet the completeness bar. Subsequent sample validation rejects a proposed buyer selector that returns nothing on all three notices.
- **FGC.** A zero-model source inspection confirmed the current-notices tab exposes 39 records with notice permalinks. One notice rendered eight filename buttons. Its first button issued a public GET that returned a PDF. This confirms a public document route for that notice. A separate URL-only retest measures delivery across the buyer.

To reproduce an isolated run, use `python -m testset.url_only_trial data/<trial> --budget <approved-USD> --url <entry>`. Reuse the trial directory to share the budget ceiling. The trial writes timings, costs, request and byte counts, tender JSON, file API checks, and a source manifest for comparison. The local controls accept `--fixture-fr` and `--fixture-ja`.
