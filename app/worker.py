"""Background worker. OWNER: A1.

- A queue of reads; WORKER_CONCURRENCY reads run at the same time (different portals).
- At most one read per portal at a time: refresh while one is queued/running returns its runId.
- A timer re-reads every portal every REFRESH_MINUTES.
- Pipeline per read:  attach (first time) -> run_recipe -> normalize -> documents ->
  health check -> store + serving,  or  healing -> repair -> re-read -> serving | failed.

Notes for other agents (Brain code called from here):
- Raise, don't return error values. The worker turns each exception into a state + reason:
    Blocked (brain/blocks.py)            -> failed, "blocked: ..."
    AttachFailed (brain/learn.py)        -> failed, "could not attach: ..."
    BudgetExceeded / LLMUnavailable      -> keep last good tenders, reason says so
    PortalCancelled (app/fetcher.py)     -> portal was deleted, run stops quietly
    anything else                        -> keep last good tenders, reason names the error
- repair() returning None means "gave up" -> failed.
- Exception messages end up in `reason` on the dashboard: write them in plain words.
- Only a healthy read is stored, so healing keeps serving the last correct tenders.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import states
from app.config import REFRESH_MINUTES, WORKER_CONCURRENCY
from app.db import DB
from app.documents import store_documents
from app.fetcher import Fetcher, PortalCancelled, PortalFetcher
from app.health import check_health
from app.models import ReadResult, Recipe, Tender
from brain.blocks import Blocked
from brain.learn import AttachFailed, learn_recipe
from brain.llm import LLM, BudgetExceeded, LLMUnavailable
from brain.normalize import normalize
from brain.repair import repair
from brain.run_recipe import run_recipe

log = logging.getLogger("watchtower.worker")


class Worker:
    def __init__(self, db: DB, fetcher: Fetcher, llm: LLM) -> None:
        self.db = db
        self.fetcher = fetcher
        self.llm = llm
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.active: dict[str, str] = {}  # portal_id -> run_id of the queued or running read
        self._tasks: list[asyncio.Task] = []

    # ----- control -----

    async def start(self) -> None:
        for _ in range(WORKER_CONCURRENCY):
            self._tasks.append(asyncio.create_task(self._consume()))
        self._tasks.append(asyncio.create_task(self._scheduler()))
        self.db.runs_close_orphans()
        # Resume portals that were mid-attach when the container stopped.
        for portal in self.db.portal_list():
            if portal["state"] in (states.INTEGRATING, states.HEALING):
                self.request_read(portal["id"])

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def request_read(self, portal_id: str) -> str:
        """Queue a read unless one is already queued or running; return its run id."""
        if portal_id in self.active:
            return self.active[portal_id]
        run_id = self.db.run_create(portal_id, "read")
        self.active[portal_id] = run_id
        self.queue.put_nowait((portal_id, run_id))
        return run_id

    def cancel(self, portal_id: str) -> None:
        """DELETE: in-flight requests for this portal stop at their next fetch."""
        self.fetcher.cancel(portal_id)

    def revive(self, portal_id: str) -> None:
        self.fetcher.uncancel(portal_id)

    # ----- loops -----

    async def _scheduler(self) -> None:
        while True:
            await asyncio.sleep(30)
            due_before = datetime.now(timezone.utc) - timedelta(minutes=REFRESH_MINUTES)
            for portal in self.db.portal_list():
                if portal["state"] == states.FAILED:
                    continue  # TODO(A1): retry failed portals now and then (e.g. every few hours)?
                last = self.db.last_read_started(portal["id"])
                if last is None or datetime.fromisoformat(last) < due_before:
                    self.request_read(portal["id"])

    async def _consume(self) -> None:
        while True:
            portal_id, run_id = await self.queue.get()
            try:
                await self._read(portal_id, run_id, allow_repair=True)
            except Exception as e:  # never let one portal kill the worker
                log.exception("read %s crashed", run_id)
                self.db.run_finish(run_id, "failed", f"internal error: {e}")
            finally:
                self.active.pop(portal_id, None)
                self.queue.task_done()

    # ----- pipeline -----

    async def _read(self, portal_id: str, run_id: str, allow_repair: bool) -> None:
        portal = self.db.portal_get(portal_id)
        if portal is None:
            self.db.run_finish(run_id, "failed", "portal was deleted")
            return
        fetch = self.fetcher.for_portal(portal_id)
        recipe = self.db.recipe_latest(portal_id)
        cost = 0.0
        try:
            if recipe is None:
                self.db.run_note(run_id, "attach: no recipe yet, asking the model to learn one")
                llm = self.llm.scoped(portal_id, run_id, "attach")
                recipe = await learn_recipe(portal["url"], fetch, llm)
                cost += llm.total_cost
                version = self.db.recipe_save(portal_id, recipe, "attach", llm.total_cost)
                self.db.run_note(run_id, f"recipe v{version} learned by {recipe.learned_by} (${llm.total_cost:.4f})")

            result = await self._execute(portal_id, recipe, fetch)
            health = check_health(self._previous(portal_id), result)
            self.db.run_note(run_id, f"read {result.raw_count} list entries, {len(result.tenders)} tenders")

            if health.ok:
                count = self.db.save_tenders(portal_id, result.tenders)
                states.set_state(self.db, portal_id, states.SERVING, f"delivering {count} tenders", run_id)
                self.db.run_finish(run_id, "ok", cost_usd=cost)
                return

            problems = "; ".join(health.problems)
            self.db.run_finish(run_id, "failed", f"health check failed: {problems}", cost)
            if allow_repair:
                await self._repair(portal_id, recipe, health, fetch)
            else:
                states.set_state(self.db, portal_id, states.FAILED, f"repaired recipe still fails: {problems}")

        except PortalCancelled:
            self.db.run_finish(run_id, "failed", "portal was deleted", cost)
        except Blocked as e:
            states.set_state(self.db, portal_id, states.FAILED, f"blocked: {e}", run_id)
            self.db.run_finish(run_id, "failed", cost_usd=cost)
        except (BudgetExceeded, LLMUnavailable) as e:
            self._keep_last_good(portal_id, run_id, f"model unavailable: {e}")
            self.db.run_finish(run_id, "failed", cost_usd=cost)
        except AttachFailed as e:
            states.set_state(self.db, portal_id, states.FAILED, f"could not attach: {e}", run_id)
            self.db.run_finish(run_id, "failed", cost_usd=cost)
        except Exception as e:
            log.exception("read %s failed", run_id)
            self._keep_last_good(portal_id, run_id, f"read failed: {type(e).__name__}: {e}")
            self.db.run_finish(run_id, "failed", cost_usd=cost)

    async def _execute(self, portal_id: str, recipe: Recipe, fetch: PortalFetcher) -> ReadResult:
        raws = await run_recipe(recipe, fetch)
        tenders: list[Tender] = []
        tried = 0
        for raw in raws:
            tender = normalize(raw, recipe.locale)
            if tender is None:
                continue
            if raw.document_links:
                tried += len(raw.document_links)
                tender.documents = await store_documents(portal_id, tender, raw.document_links, fetch)
            tenders.append(tender)
        # TODO(A2): count real document failures (store_documents should report them).
        return ReadResult(tenders=tenders, raw_count=len(raws), documents_tried=tried)

    async def _repair(self, portal_id: str, recipe: Recipe, health, fetch: PortalFetcher) -> None:
        problems = "; ".join(health.problems)
        repair_run = self.db.run_create(portal_id, "repair")
        states.set_state(self.db, portal_id, states.HEALING, f"repairing after: {problems}", repair_run)
        llm = self.llm.scoped(portal_id, repair_run, "repair")
        try:
            new_recipe = await repair(recipe, health, fetch, llm)
        except (BudgetExceeded, LLMUnavailable) as e:
            self.db.run_finish(repair_run, "failed", str(e), llm.total_cost)
            states.set_state(self.db, portal_id, states.FAILED, f"repair impossible, model unavailable: {e}")
            return
        if new_recipe is None:
            self.db.run_finish(repair_run, "failed", "repair gave up", llm.total_cost)
            states.set_state(self.db, portal_id, states.FAILED, f"repair gave up after: {problems}")
            return
        version = self.db.recipe_save(portal_id, new_recipe, "repair", llm.total_cost)
        self.db.run_finish(repair_run, "ok", f"recipe v{version} written", llm.total_cost)
        reread = self.db.run_create(portal_id, "read")
        await self._read(portal_id, reread, allow_repair=False)

    def _previous(self, portal_id: str) -> ReadResult | None:
        stored = self.db.tenders_for(portal_id)
        if not stored:
            return None
        return ReadResult(tenders=[Tender.model_validate(t) for t in stored], raw_count=len(stored))

    def _keep_last_good(self, portal_id: str, run_id: str, reason: str) -> None:
        """A read failed for a reason that isn't the recipe. Be honest about it."""
        portal = self.db.portal_get(portal_id)
        if portal is None:
            return
        if portal["state"] == states.INTEGRATING:
            states.set_state(self.db, portal_id, states.FAILED, reason, run_id)
        else:
            # Keep the state, but the reason must say the latest data may be stale.
            self.db.portal_update(portal_id, reason=f"{reason} (serving last good tenders)")
            self.db.run_note(run_id, reason)
