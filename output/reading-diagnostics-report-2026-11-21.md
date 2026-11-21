# Reading diagnostics — 21 November 2026

Checkout: `02f3fad`. I added nine offline tests in `tests/test_reading_diagnostics.py`. They exercise the normal recipe reader, normalization, health check and worker with deterministic HTTP responses; they make no public requests or model calls. Seven are `xfail(strict=True)` because they specify desired behavior that this checkout lacks. The two passing controls show the fixture setup can read a complete feed and can detect a full login page on an initial file download. A future fix makes a strict xfail become `XPASS`, which fails the suite until the marker is removed.

## Follow-up: fixes applied

The defects below have now been fixed in the working tree, and all nine diagnostic tests are regular passing tests. Health rejects malformed or oversized short fields and any missing selected-row ID; verified empty results require a JSON total of exactly zero. JSON list recipes can follow an HTML detail page and retain list fields. A selected document source is rechecked even while its bytes are locally fresh, and `text/html` responses are classified as web pages even when they are fragments. On archive-dominated first reads (at least four rows and more closed than open), files for closed notices are skipped; ordinary lists still deliver files for closed notices as required by the judge fixture. The German `Art` notice label now maps to `contractNature`.

Verification after those fixes: the nine diagnostics plus judge regressions pass (**19 passed**); the full suite passes (**437 passed, 20 skipped**). See `reading-fixed-focus-log.txt` and `reading-fixed-full-log.txt` for the final output. Earlier logs below record the pre-fix baseline and remain as historical evidence.

## Test-by-test log

| Test | Outcome | Observation and implication |
| --- | --- | --- |
| `test_complete_json_notice_feed_is_a_healthy_control` | **PASS** | Five distinct notices with IDs, titles, buyer and deadline pass the current read and health path. |
| `test_stylesheet_text_in_buyer_cannot_verify_a_read` | **PASS after fix** | The original test showed a buyer value over 50 KB of CSS-like text passed health. Health now rejects overlong fields and CSS/JS patterns. |
| `test_one_unidentified_notice_makes_coverage_unverified` | **PASS after fix** | The original test showed four of five notices could pass at the former 80% ID threshold. The read now fails if any selected row is lost during normalization. |
| `test_json_listing_can_open_html_notice_details` | **PASS after fix** | The original test raised `could not parse portal response as json_api` on an HTML detail. List and detail now use their actual response formats, and list fields are preserved. |
| `test_authoritative_empty_feed_is_a_verified_empty_read` | **PASS after fix** | The original test showed `items=[]`, `total=0` was always unhealthy. The reader now carries an explicit verified-empty signal from that authoritative JSON count into health. |
| `test_login_wall_on_known_documents_is_detected_on_next_read` | **PASS after fix** | The original test stayed `serving` without requesting a recently stored file. A selected representative file is now requested on the next read and the login wall is detected. |
| `test_document_login_on_initial_read_is_detected` | **PASS** | A full HTML login page returned at the document URL on the first read makes the portal `failed` with a login reason. This isolates the revalidation gap above. |
| `test_html_fragment_cannot_be_published_as_a_pdf` | **PASS after fix** | The original test showed `Content-Type: text/html` at a PDF URL was stored. Document checks now honor the response content type, including for fragments. |
| `test_first_read_skips_files_for_explicitly_closed_archive` | **PASS after fix** | The original archive-heavy first read fetched all four PDFs. It now skips files for closed notices when the listing is archive-dominated; regular listings still deliver closed-notice files. |

The original pre-fix baseline was **2 passed, 7 xfailed**; with `--runxfail`, the seven underlying failures were confirmed at the intended assertions or parser boundary. After fixing them, the normal run is **9 passed, 0 xfailed**.

## Existing-suite regression discovered during verification

The pre-fix full suite produced **426 passed, 20 skipped, 7 expected failures, 4 ordinary failures**. All four ordinary failures were parameterizations of `tests/test_judge_regressions.py::test_judge_portal_url_to_verified_file_bytes` and stopped at `KeyError: 'contractNature'`. The judge detail fixture labels this fact `Art`; `brain/notice_fields.py` recognized `Art des Auftrags` but not `Art`. Adding that exact label fixed the extraction, and all four judge cases now pass. The post-fix full suite is **437 passed, 20 skipped**.

## Saved commands and outputs

The environment's `.venv` launcher references a removed Python installation. Tests used bundled Python 3.12.14 with `.venv/Lib/site-packages` on `PYTHONPATH`, pytest cache disabled, and temporary directories inside the workspace.

- Final: `python -m pytest tests/test_reading_diagnostics.py tests/test_judge_regressions.py -v -p no:cacheprovider` → **19 passed**. Per-test output: `reading-fixed-focus-log.txt`.
- Final: `python -m pytest -q -p no:cacheprovider` → **437 passed, 20 skipped**. Full output: `reading-fixed-full-log.txt`.
- Historical pre-fix outputs remain in `reading-diagnostics-log.txt`, `reading-diagnostics-raw-log.txt`, `reading-full-suite-log.txt`, and `reading-judge-isolation-log.txt`.

The command summaries above omit the explicit bundled Python path and `--basetemp` argument; the saved logs contain the executable identity and exact test results.
