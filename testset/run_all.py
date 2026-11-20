"""Collect outcomes from a running Watchtower API. Usage: python testset/run_all.py --help."""
import argparse
import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx
import yaml
from lxml import html

HERE = Path(__file__).resolve().parent


@dataclass
class Outcome:
    name: str
    state: str = "unobserved"
    reason: str = ""
    tenders: int | None = None
    documents: int | None = None
    cost: float | None = None
    incomplete: bool = False


async def observe(client, entry, timeout, poll_interval=2, refresh=False):
    row = Outcome(entry["name"])
    try:
        response = await client.post("/portals", json={"url": entry["url"]})
        response.raise_for_status()
        path = "/portals/" + response.json()["id"]
        run_id = None
        if refresh:
            run_id = (await client.post(path + "/refresh")).raise_for_status().json()["runId"]
        deadline = monotonic() + timeout
        while True:
            portal = (await client.get(path)).raise_for_status().json()
            row.state, row.reason = portal["state"], portal["reason"] or ""
            if row.state in {"serving", "failed"}:
                if run_id is None:
                    break
                runs = (await client.get(path + "/runs")).raise_for_status().json()
                if any(r["id"] == run_id and r["result"] for r in runs):
                    # The portal could have changed between the two GETs.
                    run_id = None
                    continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                row.reason = f"Observation timed out. {row.reason}"
                row.incomplete = True
                break
            await asyncio.sleep(min(poll_interval, remaining))
        tenders = (await client.get(path + "/tenders")).raise_for_status().json()
        row.tenders = len(tenders)
        row.documents = sum(len(t["documents"]) for t in tenders)
        detail = (await client.get("/ui" + path)).raise_for_status()
        costs = html.fromstring(detail.content).xpath("//*[@data-model-cost-usd]/@data-model-cost-usd")
        if not costs:
            raise ValueError("dashboard did not supply a model cost")
        row.cost = float(costs[0])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        row.reason = f"{row.reason} Observation error: {exc}".strip()
        row.incomplete = True
    return row


def markdown(rows):
    def cell(value):
        return " ".join(str(value).split()).replace("|", r"\|") if value is not None else "—"

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Watchtower portal results", "", f"Observed {timestamp}. Counts include all held tenders "
        "and their documents. Cost is cumulative recorded model spend per portal in this data folder.",
        "", "| Portal | State | Reason | Tenders | Documents | Model USD |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        values = (row.name, row.state, row.reason, row.tenders, row.documents,
                  f"{row.cost:.6f}" if row.cost is not None else None)
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    return "\n".join(lines) + "\n"



def model_usage(path):
    """Group the ledger by completed run, including reads that made no model calls."""
    sql = """
        WITH costs AS (
            SELECT run_id, count(*) AS calls, sum(cost_usd) AS spend
            FROM llm_calls GROUP BY run_id
        )
        SELECT CASE WHEN kind = 'repair' THEN 'repair'
                    WHEN notes LIKE '%attach: no recipe yet%' THEN 'attach'
                    ELSE 'read' END AS operation,
               result, count(*), sum(coalesce(calls, 0)), sum(coalesce(spend, 0))
        FROM runs LEFT JOIN costs ON costs.run_id = runs.id
        WHERE finished_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM llm_reservations WHERE run_id = runs.id)
        GROUP BY operation, result ORDER BY operation, result
    """
    lines = ["", "## Model usage", "",
             "Completed runs in this data folder, grouped by outcome. Runs with unresolved "
             "usage reservations are excluded. Amounts come from the model call ledger.", "",
             "| Operation | Result | Runs | Model calls | Total USD | USD per run |",
             "|---|---|---:|---:|---:|---:|"]
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for operation, result, runs, calls, spend in db.execute(sql):
            lines.append(f"| {operation} | {result} | {runs} | {calls} | {spend:.6f} | {spend/runs:.6f} |")
    return "\n".join(lines) + "\n"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="also summarize usage from this mounted Watchtower database")
    parser.add_argument("--refresh", action="store_true", help="request a new read and wait for its run")
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    parser.add_argument("--portals", type=Path, default=HERE / "portals.yaml")
    parser.add_argument("--mock-url", help="also register a mock URL reachable from the container")
    parser.add_argument("--output", type=Path, help="default: testset/results.md for the public set")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait per portal")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.portals.resolve() != HERE / "portals.yaml" and args.output is None:
        parser.error("specify --output for a custom or private set")
    entries = yaml.safe_load(args.portals.read_text(encoding="utf-8"))["portals"]
    if args.mock_url:
        entries.append({"name": "mock", "url": args.mock_url})
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        rows = await asyncio.gather(*(observe(client, entry, args.timeout, refresh=args.refresh) for entry in entries))
    output = args.output or HERE / "results.md"
    report = markdown(rows)
    if args.db:
        report += model_usage(args.db)
    output.write_text(report, encoding="utf-8")
    print(f"Wrote {len(rows)} portals to {output}")
    return int(any(row.incomplete for row in rows))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
