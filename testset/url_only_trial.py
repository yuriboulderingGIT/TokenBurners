"""Run a fresh URL-only API trial and keep per-portal source evidence for independent checking.

Example: python -m testset.url_only_trial data/blind-trial --budget 1 --url https://buyer.example/list
Use --fixture-fr for the local judge fixture. Reuse the trial directory to share the original budget ceiling.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def reader_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(path for folder in ("app", "brain") for path in (root / folder).rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class Meter(httpx.AsyncBaseTransport):
    """Count actual response body bytes, including redirects and partial streams."""
    def __init__(self, upstream, folder):
        self.upstream, self.folder = upstream, folder
        self.requests = self.received = 0
        self.manifest = []

    async def handle_async_request(self, request):
        self.requests += 1
        number = self.requests
        response = await self.upstream.handle_async_request(request)
        stream, meter = response.stream, self
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                digest = hashlib.sha256()
                size = 0
                with (meter.folder / f"{number}.body").open("wb") as out:
                    async for chunk in stream:
                        size += len(chunk)
                        meter.received += len(chunk)
                        digest.update(chunk)
                        out.write(chunk)
                        yield chunk
                meter.manifest.append({"request": number, "url": str(request.url), "status": response.status_code,
                                       "type": response.headers.get("Content-Type"), "encoding": response.headers.get("Content-Encoding"),
                                       "bytes": size, "sha256": digest.hexdigest()})

            async def aclose(self):
                await stream.aclose()
        response.stream = Body()
        return response

    async def aclose(self):
        await self.upstream.aclose()


async def run(args):
    load_dotenv()
    ledger_path = Path(os.getenv("DATA_DIR", "data")).resolve() / "watchtower.db"
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(dir=directory, prefix="attempt-"))
    fingerprint = reader_fingerprint()
    os.environ.update(DATA_DIR=str(work), LLM_CACHE="0")
    from app import main
    from app.config import LLM_BUDGET_USD
    from app.db import DB, db
    from app.destinations import PublicHTTPTransport
    from app.fetcher import Fetcher
    from app.worker import Worker
    from brain.llm import LLM
    from judge import fixture_fr

    if not ledger_path.is_file():
        raise RuntimeError("The configured development budget ledger must exist")
    class Ledger(DB):
        def __init__(self):
            self._conn = sqlite3.connect(ledger_path.as_uri()+"?mode=rw", uri=True,
                                         check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()

        def run_note(self, run_id, text):
            db.run_note(run_id, text)

        def llm_log(self, *a, **kw):
            super().llm_log(*a, **kw)
            db.llm_log(*a, **{k:v for k,v in kw.items() if k != "reservation"})
    ledger = Ledger()
    budget = directory / "ceiling.json"
    if not budget.exists():
        with budget.open("x", encoding="utf-8") as stream:
            json.dump({"start": ledger.llm_total_cost(), "allowance": args.budget}, stream)
    allowance = json.loads(budget.read_text(encoding="utf-8"))
    model = LLM(ledger, budget_usd=min(LLM_BUDGET_USD, allowance["start"] + min(args.budget, allowance["allowance"])), cache_dir=None)
    limits = asyncio.Semaphore(2)
    async def trial(url):
        async with limits:
            folder = work / re.sub(r"[^a-zA-Z0-9.-]", "-", urlsplit(url).netloc)
            folder.mkdir()
            meter = Meter(httpx.AsyncHTTPTransport() if args.fixture_fr or args.fixture_ja else PublicHTTPTransport(), folder)
            fetcher = Fetcher(transport=meter)
            worker = Worker(db, fetcher, model)
            started = time.monotonic()
            print("Starting", url, flush=True)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://api") as api:
                    main.app.state.worker = worker
                    pid = (await api.post("/portals", json={"url":url})).json()["id"]
                    job = worker.queue.get_nowait()
                    timed_out = False
                    try:
                        await asyncio.wait_for(worker._handle(*job), args.seconds)
                    except TimeoutError:
                        fetcher.cancel(pid)
                        timed_out = True
                    elapsed = round(time.monotonic()-started, 2)
                    portal = (await api.get(f"/portals/{pid}")).json()
                    rows = (await api.get(f"/portals/{pid}/tenders")).json()
                    save(folder/"delivered.json", rows)
                    failures = []
                    files = 0
                    for row in rows:
                        for doc in row["documents"]:
                            files += 1
                            digest = hashlib.sha256()
                            size = 0
                            async with api.stream("GET",doc["path"]) as received:
                                async for chunk in received.aiter_bytes():
                                    digest.update(chunk)
                                    size += len(chunk)
                            if received.status_code != 200 or size != doc["sizeBytes"] or digest.hexdigest() != doc["sha256"]:
                                failures.append([row["id"], doc["fileName"]])
                    usage = ledger.all("SELECT cost_usd,purpose FROM llm_calls WHERE portal_id=?", (pid,))
                    reserved = ledger.one("SELECT COALESCE(SUM(max_cost_usd),0) AS n FROM llm_reservations WHERE portal_id=?", (pid,))
                    result = dict(portal=portal, reader_sha256=fingerprint, seconds=elapsed, timeout=timed_out,
                                  requests=meter.requests, bytes=meter.received,
                                  time_to_first_publication_seconds=elapsed if portal["state"]=="serving" else None,
                                  tenders=len(rows), files=files, file_api_errors=failures, model_calls=len(usage),
                                  usd=sum(call["cost_usd"] for call in usage), reserved_usd=reserved["n"])
                    save(folder/"runs.json", [dict(row) for row in db.runs_for(pid)])
                    save(folder/"result.json", result)
                    print(json.dumps(result), flush=True)
            finally:
                save(folder/"sources.json", meter.manifest)
                await fetcher.aclose()
    with ExitStack() as stack:
        urls = args.url or []
        if args.fixture_fr or args.fixture_ja:
            if urls:
                raise ValueError("Run the local fixture separately")
            from testset import japanese_fixture
            port, handler = (8093, fixture_fr.H) if args.fixture_fr else (8094, japanese_fixture.Handler)
            server = stack.enter_context(ThreadingHTTPServer(("localhost", port), handler))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            stack.callback(server.shutdown)
            urls = ["http://localhost:8093/consultations" if args.fixture_fr else japanese_fixture.ROOT + "/notices"]
        if len({urlsplit(url).hostname for url in urls}) != len(urls):
            raise ValueError("Select distinct portal hosts")
        await asyncio.gather(*(trial(url) for url in urls))
    print("Evidence", work, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    parser.add_argument("--budget",type=float,required=True)
    parser.add_argument("--url",action="append")
    parser.add_argument("--seconds",type=int,default=720)
    fixture = parser.add_mutually_exclusive_group()
    fixture.add_argument("--fixture-fr",action="store_true")
    fixture.add_argument("--fixture-ja",action="store_true")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(parser.parse_args()))
