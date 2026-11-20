"""Rehearse B10/B15 offline or run the live checks with the hand-in key.

From the repo root:

    python -m brain.proof --rehearse
    python -m brain.proof --live [--only mock|real] [--output NEW_DIRECTORY]

The mock runs inside this process. Its SPA requires Playwright Chromium. Each run
saves its tenders, files and results.json in a new directory. Live calls share the
application's budget ledger in DATA_DIR so another run cannot reset the cap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.worker import Worker


REAL_TARGETS = ("etenders-ie", "vergabemarktplatz-nrw-de", "dtvp-de")
SSR_URL = "http://ssr.mock.test/tenders"
SPA_URL = "http://spa.mock.test/tenders"


def real_targets() -> dict[str, str]:
    import yaml

    manifest = Path(__file__).resolve().parents[1] / "testset/portals.yaml"
    portals = yaml.safe_load(manifest.read_text(encoding="utf-8"))["portals"]
    urls = {p["name"]: p["url"] for p in portals if p["kind"] == "ssr"}
    return {name: urls[name] for name in REAL_TARGETS}


async def measure(worker: Worker, case: str, portal_id: str, *, rehearsal: bool = False) -> dict:
    """Run the production worker to completion, including any repair and re-read."""
    db = worker.db
    ledger = db if rehearsal else worker.llm.db
    print(f"Starting {case}", flush=True)
    last_good = db.tenders_for(portal_id) if case == "mock-captcha" else None
    first_run = worker.request_read(portal_id)
    await worker.queue.join()
    runs = db.all(
        "SELECT id, notes FROM runs WHERE portal_id = ? AND rowid >= "
        "(SELECT rowid FROM runs WHERE id = ?) ORDER BY rowid", (portal_id, first_run),
    )
    calls = []
    reserved = []
    transitions = []
    for run in runs:
        transitions.extend(re.findall(r"\] state (\w+ -> \w+):", run["notes"]))
        calls.extend(dict(r) for r in ledger.all(
            "SELECT purpose, model, tokens_in, tokens_out, cost_usd FROM llm_calls WHERE run_id = ? "
            "ORDER BY rowid", (run["id"],),
        ))
        reserved.extend(r["max_cost_usd"] for r in ledger.all(
            "SELECT max_cost_usd FROM llm_reservations WHERE run_id = ?", (run["id"],),
        ))

    portal = db.portal_get(portal_id)
    # The documents table can include obsolete files or partial failed reads. Count
    # only files the stored tender response actually references and can still serve.
    tenders = db.tenders_for(portal_id)
    files = {}
    for tender in tenders:
        names = []
        for document in tender["documents"]:
            stored = db.document_get(document["path"].removeprefix("/files/"))
            if stored and Path(stored["path_on_disk"]).is_file():
                names.append(document["fileName"])
        files[tender["id"]] = names
    document_count = sum(len(names) for names in files.values())
    if case == "mock-captcha":
        checks = {
            "failed on CAPTCHA": portal["state"] == "failed" and "captcha" in portal["reason"].lower(),
            "retained last good tenders and files": bool(last_good) and last_good == tenders
            and document_count == sum(len(t["documents"]) for t in last_good),
        }
    else:
        purpose = "repair" if case == "mock-layout-v2" else "attach"
        checks = {
            "serving": portal["state"] == "serving",
            "has tenders and downloaded documents": bool(tenders and document_count),
        }
        if not rehearsal:
            checks[f"recorded {purpose} model usage"] = any(call["purpose"] == purpose for call in calls)
        if case.startswith("mock-"):
            from mock_portal import server

            expected = {t["id"]: {"tender-documents.pdf"} for t in server.TENDERS}
            expected[server.PACKAGE_TENDER].update(server.PACKAGE_FILES)
            checks["all mock IDs and documents"] = {k: set(v) for k, v in files.items()} == expected
        if case == "mock-layout-v2":
            checks["healed after layout change"] = transitions == ["serving -> healing", "healing -> serving"]
        if case == "mock-spa":
            recipe = db.recipe_latest(portal_id)
            checks["discovered JSON feed"] = recipe is not None and recipe.kind == "json_api"

    checks["all model costs settled"] = not reserved
    if rehearsal:
        checks["no billed model calls"] = not calls
    costs = {}
    for call in calls:
        purpose = call["purpose"]
        costs[purpose] = costs.get(purpose, 0.0) + call["cost_usd"]
    return {
        "case": case, "url": portal["url"], "portal_id": portal_id, "state": portal["state"],
        "reason": portal["reason"], "stored_tenders": len(tenders), "documents": document_count,
        "runs": [r["id"] for r in runs], "transitions": transitions, "llm_calls": calls,
        "recorded_cost_usd": sum(costs.values()), "cost_by_purpose_usd": costs,
        "pending_reservations_usd": sum(reserved), "checks": checks, "passed": all(checks.values()),
    }


async def mock_cases(worker: Worker, record: Callable[[dict], None], *, rehearsal: bool = False) -> None:
    from mock_portal import server

    server.switch(reset=True)
    try:
        ssr = worker.db.portal_create(SSR_URL)
        record(await measure(worker, "mock-ssr", ssr, rehearsal=rehearsal))
        server.switch(variant="spa")
        record(await measure(worker, "mock-spa", worker.db.portal_create(SPA_URL), rehearsal=rehearsal))
        server.switch(variant="ssr", layout="v2")
        if worker.db.portal_get(ssr)["state"] == "serving":
            record(await measure(worker, "mock-layout-v2", ssr, rehearsal=rehearsal))
        else:
            record({"case": "mock-layout-v2", "state": "skipped", "passed": False,
                    "reason": "The v1 attach did not reach serving, so there is no healthy baseline to repair."})
        server.switch(variant="captcha")
        record(await measure(worker, "mock-captcha", ssr, rehearsal=rehearsal))
    finally:
        server.switch(reset=True)


async def run(only: str, directory: Path, *, rehearsal: bool, ledger_path: Path | None) -> bool:
    import httpx

    from app.config import MIN_REQUEST_INTERVAL
    from app.db import DB, db
    from app.fetcher import Fetcher
    from app.worker import Worker
    from brain.llm import LLM

    if rehearsal:
        from brain.proof_fixture import RehearsalModel

        if only != "mock":
            raise ValueError("offline rehearsal uses only the local mock")
        llm = RehearsalModel()
        report = {"mode": "offline rehearsal", "attach_model": llm.model_name,
                  "repair_model": llm.strong_model_name, "budget_usd": 0, "cases": []}
    else:
        if ledger_path is None:
            raise ValueError("live runs require the application's budget ledger")
        llm = LLM(DB(ledger_path), cache_dir=None)
        report = {"mode": "live", "attach_model": llm.cheap_model, "repair_model": llm.strong_model,
                  "budget_usd": llm.budget_usd, "budget_ledger": str(ledger_path), "cases": []}
    report_file = directory / "results.json"

    def record(row: dict) -> None:
        report["cases"].append(row)
        draft = report_file.with_suffix(".tmp")
        draft.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        draft.replace(report_file)
        print(f"{row['case']}: {row['state']} ({row['reason']})", flush=True)

    for group in (("mock", "real") if only == "all" else (only,)):
        transport = None
        if group == "mock":
            from mock_portal import server

            transport = httpx.ASGITransport(app=server.app)
        fetcher = Fetcher(min_interval=0 if rehearsal else max(1.0, MIN_REQUEST_INTERVAL), transport=transport)
        worker = Worker(db, fetcher, llm)
        try:
            await worker.start()
            if group == "mock":
                await mock_cases(worker, record, rehearsal=rehearsal)
            else:
                for name, url in real_targets().items():
                    record(await measure(worker, name, db.portal_create(url)))
        finally:
            await worker.stop()
            await fetcher.aclose()
    return all(row["passed"] for row in report["cases"])


def main() -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rehearse", action="store_true", help="local mock and canned replies, no key required")
    mode.add_argument("--live", action="store_true", help="use the configured model API and spend its budget")
    parser.add_argument("--only", choices=("mock", "real", "all"), help="which targets to run in live mode")
    parser.add_argument("--output", type=Path, help="new directory for the tender database, files and results.json")
    args = parser.parse_args()
    load_dotenv()
    if args.rehearse and args.only:
        parser.error("--only selects targets for --live")
    if args.live and not os.getenv("OPENAI_API_KEY", "").strip():
        parser.error("Set OPENAI_API_KEY to the hand-in project key for this real-model run.")
    ledger_path = (Path(os.getenv("DATA_DIR", "./data")).resolve() / "watchtower.db") if args.live else None
    if args.output:
        directory = args.output.resolve()
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            parser.error("--output must name a new directory")
    else:
        parent = Path("data/proofs")
        parent.mkdir(parents=True, exist_ok=True)
        prefix = "rehearsal-" if args.rehearse else "live-"
        directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent)).resolve()
    # app.db creates its singleton on import. Configure paths before importing the
    # app, and keep scheduled refreshes out of this one-shot ledger.
    os.environ.update(DATA_DIR=str(directory), REFRESH_MINUTES="1000000", FAILED_RETRY_MINUTES="1000000")
    print(f"Evidence directory: {directory}", flush=True)
    target = "mock" if args.rehearse else (args.only or "all")
    return 0 if asyncio.run(run(target, directory, rehearsal=args.rehearse, ledger_path=ledger_path)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
