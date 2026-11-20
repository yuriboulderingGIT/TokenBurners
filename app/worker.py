"""Background worker. OWNER: A1.

- A queue of reads; WORKER_CONCURRENCY reads run at the same time (different portals).
- At most one read per portal at a time: refresh while one is queued/running returns its runId.
- A timer re-reads every portal every REFRESH_MINUTES. Failed portals get a plain re-read every
  FAILED_RETRY_MINUTES; one that never attached is left alone until a manual refresh, unless the
  portal was unreachable before any model call (TemporaryAttachFailure).
- Pipeline per read:  attach (first time) -> run_recipe -> normalize -> documents ->
  health check -> store + serving,  or  healing -> repair -> re-read -> serving | failed.

Notes for other agents (Brain code called from here):
- Raise, don't return error values. The worker turns each exception into a state + reason:
    Blocked (brain/blocks.py)            -> failed, "blocked: ..."
    AttachFailed (brain/learn.py)        -> failed, "could not attach: ..."
    RecipeReadError (brain/run_recipe.py) -> failed read, attempt repair if allowed
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

import httpx

from app import states
from app.config import (
    FAILED_RETRY_MINUTES, FIRST_READ_TRIES, INTEGRATING_RETRY_MINUTES, REFRESH_MINUTES, WORKER_CONCURRENCY,
)
from app.db import DB
from app.documents import store_documents
from app.fetcher import Fetcher, PortalCancelled, PortalFetcher, RunPageCache
from app.health import check_health
from app.models import Health, ReadResult, Recipe, Tender
from brain.blocks import Blocked
from brain.learn import AttachFailed, TemporaryAttachFailure, learn_recipe
from brain.llm import LLM, BudgetExceeded, LLMUnavailable
from brain.normalize import normalize
from brain.repair import repair
from brain.run_recipe import PaginationError, RecipeReadError, run_recipe

log = logging.getLogger("watchtower.worker")
_TEMPORARY_ATTACH = "could not attach (temporary portal error): "


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
            self._queue_due_reads()

    def _queue_due_reads(self) -> None:
        for portal in self.db.portal_list():
            if (portal["state"] == states.FAILED and portal["recipe_version"] is None
                    and not (portal["reason"] or "").startswith(_TEMPORARY_ATTACH)):
                # It never attached, so a timed retry could only ask the model again (and get
                # charged every FAILED_RETRY_MINUTES). Timed retries must cost nothing: only a
                # manual refresh tries the attach again.
                continue
            if self._due(portal["id"], self._retry_minutes(portal)):
                self.request_read(portal["id"])

    @staticmethod
    def _retry_minutes(portal) -> float:
        if portal["state"] == states.FAILED:
            return FAILED_RETRY_MINUTES
        if portal["state"] == states.INTEGRATING:
            # Nothing is delivered yet: a first read that hit a temporary error is tried again soon.
            return min(INTEGRATING_RETRY_MINUTES, REFRESH_MINUTES)
        return REFRESH_MINUTES

    def _due(self, portal_id: str, minutes: float) -> bool:
        last = self.db.last_read_started(portal_id)
        due_before = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return last is None or datetime.fromisoformat(last) < due_before

    async def _consume(self) -> None:
        while True:
            portal_id, run_id = await self.queue.get()
            try:
                await self._handle(portal_id, run_id)
            finally:
                self.queue.task_done()

    async def _handle(self, portal_id: str, run_id: str) -> None:
        """One queued read. A failed portal gets a plain retry: no repair, so no model cost."""
        try:
            portal = self.db.portal_get(portal_id)
            retry = portal is not None and portal["state"] == states.FAILED
            if retry and portal["recipe_version"] is not None:
                self.db.run_note(run_id, "retry of a failed portal: plain read, no repair")
            await self._read(portal_id, run_id, allow_repair=not retry, retry=retry)
        except Exception as e:  # never let one portal kill the worker
            log.exception("read %s crashed", run_id)
            self.db.run_finish(run_id, "failed", f"internal error: {e}")
        finally:
            self.active.pop(portal_id, None)

    # ----- pipeline -----

    async def _read(
        self, portal_id: str, run_id: str, allow_repair: bool, retry: bool = False, *,
        repaired_recipe: Recipe | None = None, repair_cost_usd: float = 0.0,
    ) -> bool:
        portal = self.db.portal_get(portal_id)
        if portal is None:
            self.db.run_finish(run_id, "failed", "portal was deleted")
            return False
        recipe = repaired_recipe if repaired_recipe is not None else self.db.recipe_latest(portal_id)
        # Attach validates the whole listing; the first read in the same run reuses those pages.
        fetch = RunPageCache(self.fetcher, portal_id) if recipe is None else self.fetcher.for_portal(portal_id)
        cost = 0.0
        try:
            if recipe is None:
                if retry:
                    states.set_state(self.db, portal_id, states.INTEGRATING, "retrying attach: locating the tender listing", run_id)
                self.db.run_note(run_id, "attach: no recipe yet, asking the model to learn one")
                llm = self.llm.scoped(portal_id, run_id, "attach")
                recipe = await learn_recipe(portal["url"], fetch, llm)
                cost += llm.total_cost
                version = self.db.recipe_save(portal_id, recipe, "attach", llm.total_cost)
                self.db.run_note(run_id, f"recipe v{version} learned by {recipe.learned_by} (${llm.total_cost:.4f})")

            if portal["state"] == states.INTEGRATING or portal["recipe_version"] is None:
                states.set_state(self.db, portal_id, states.INTEGRATING,
                                 "recipe ready, reading tenders and documents for the first time", run_id)

            pagination_page = None
            try:
                result = await self._execute(portal_id, recipe, fetch)
            except RecipeReadError as exc:
                health = Health(ok=False, problems=[str(exc)])
                if isinstance(exc, PaginationError):
                    pagination_page = exc.page_sample
            else:
                health = check_health(self._previous(portal_id), result)
                self.db.run_note(run_id, f"read {result.raw_count} list entries, {len(result.tenders)} tenders")
                if health.ok:
                    count = self.db.save_tenders(
                        portal_id, result.tenders, repaired_recipe=repaired_recipe, repair_cost_usd=repair_cost_usd,
                    )
                    files = sum(len(tender.documents) for tender in result.tenders)
                    file_label = "document" if files == 1 else "documents"
                    reason = f"delivering {count} tenders. Latest read verified {files} {file_label}"
                    states.set_state(self.db, portal_id, states.SERVING, reason, run_id)
                    self.db.run_finish(run_id, "ok", cost_usd=cost)
                    return True

            problems = "; ".join(health.problems)
            self.db.run_finish(run_id, "failed", f"health check failed: {problems}", cost)
            if allow_repair:
                await self._repair(
                    portal_id, recipe, health, fetch,
                    portal_url=portal["url"], pagination_page=pagination_page,
                )
            elif retry:
                states.set_state(self.db, portal_id, states.FAILED, f"still failing on retry: {problems}")
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
            reason = str(e).strip() or "no working extraction recipe was found"
            prefix = _TEMPORARY_ATTACH if isinstance(e, TemporaryAttachFailure) else "could not attach: "
            states.set_state(self.db, portal_id, states.FAILED, prefix + reason, run_id)
            self.db.run_finish(run_id, "failed", cost_usd=cost)
        except Exception as e:
            log.exception("read %s failed", run_id)
            # No repair and no retry = the re-read right after a repair (see "repaired recipe still fails").
            self._keep_last_good(
                portal_id, run_id, f"read failed: {type(e).__name__}: {e}", repaired_recipe is not None,
                portal_hiccup=isinstance(e, httpx.HTTPError),
            )
            self.db.run_finish(run_id, "failed", cost_usd=cost)
        return False

    async def _execute(self, portal_id: str, recipe: Recipe, fetch: PortalFetcher) -> ReadResult:
        raws = await run_recipe(recipe, fetch)
        tenders: list[Tender] = []
        tender_ids_with_document_links: set[str] = set()
        tried = failed = blocked = 0
        block_kind: str | None = None
        for raw in raws:
            tender = normalize(raw, recipe.locale)
            if tender is None:
                continue
            if raw.document_links:
                tender_ids_with_document_links.add(tender.id)
                links = list(dict.fromkeys(raw.document_links))
                stored = await store_documents(self.db, portal_id, tender, links, fetch)
                tender.documents = stored.documents
                tried += len(links)
                failed += stored.failed + stored.blocked
                blocked += stored.blocked
                block_kind = block_kind or stored.block_kind
            tenders.append(tender)
        if tried and blocked == tried:
            # Every document sits behind a login or captcha: the portal can't be delivered.
            raise Blocked(block_kind or "login", "the tender documents can't be downloaded without it")
        return ReadResult(
            tenders=tenders, raw_count=len(raws), documents_tried=tried, documents_failed=failed,
            tender_ids_with_document_links=tender_ids_with_document_links,
        )

    async def _repair(
        self, portal_id: str, recipe: Recipe, health: Health, fetch: PortalFetcher, *,
        portal_url: str,
        pagination_page: dict[str, str] | None = None,
    ) -> None:
        problems = "; ".join(health.problems)
        repair_run = self.db.run_create(portal_id, "repair")
        self.active[portal_id] = repair_run  # a refresh now returns the run that is still going
        states.set_state(self.db, portal_id, states.HEALING, f"repairing after: {problems}", repair_run)
        llm = self.llm.scoped(portal_id, repair_run, "repair")
        try:
            previous = self._previous(portal_id)
            new_recipe = await repair(
                recipe, health, fetch, llm, portal_url=portal_url, pagination_page=pagination_page,
                stored_tenders=previous.tenders if previous else None,
            )
        except (BudgetExceeded, LLMUnavailable) as e:
            self.db.run_finish(repair_run, "failed", str(e), llm.total_cost)
            states.set_state(self.db, portal_id, states.FAILED, f"repair impossible, model unavailable: {e}")
            return
        except Blocked:
            # _read turns this into failed + "blocked: ..."; the repair run must not stay "running".
            self.db.run_finish(repair_run, "failed", "stopped: the portal now asks for a login or captcha", llm.total_cost)
            raise
        except PortalCancelled:
            self.db.run_finish(repair_run, "failed", "portal was deleted", llm.total_cost)
            return
        except Exception as e:  # a crash in repair must not leave the portal in healing
            log.exception("repair %s crashed", repair_run)
            self.db.run_finish(repair_run, "failed", f"repair crashed: {e}", llm.total_cost)
            states.set_state(self.db, portal_id, states.FAILED, f"repair crashed: {type(e).__name__}: {e}")
            return
        if new_recipe is None:
            self.db.run_finish(repair_run, "failed", "repair gave up", llm.total_cost)
            states.set_state(self.db, portal_id, states.FAILED, f"repair gave up after: {problems}")
            return
        reread = self.db.run_create(portal_id, "read")
        self.active[portal_id] = reread
        self.db.run_note(repair_run, "checking the replacement recipe on a full read")
        if await self._read(
            portal_id, reread, allow_repair=False, repaired_recipe=new_recipe, repair_cost_usd=llm.total_cost,
        ):
            self.db.run_finish(repair_run, "ok", f"recipe v{new_recipe.version} verified", llm.total_cost)
        else:
            self.db.run_finish(repair_run, "failed", "replacement recipe failed its full read", llm.total_cost)

    def _previous(self, portal_id: str) -> ReadResult | None:
        """What the last healthy read saw. Tenders kept as closed after they left the list don't count."""
        stored = self.db.tenders_listed_last(portal_id)
        if not stored:
            return None
        return ReadResult(tenders=[Tender.model_validate(t) for t in stored], raw_count=len(stored))

    def _failed_reads_before(self, portal_id: str, run_id: str) -> int:
        """Failed reads in a row right before this one (this run is still running, so skip it)."""
        count = 0
        for run in self.db.runs_for(portal_id, limit=FIRST_READ_TRIES + 5):
            if run["id"] == run_id or run["kind"] != "read":
                continue
            if run["result"] != "failed":
                break
            count += 1
        return count

    def _keep_last_good(
        self, portal_id: str, run_id: str, reason: str, after_repair: bool = False, portal_hiccup: bool = False,
    ) -> None:
        """A read failed for a reason that isn't the recipe. Be honest about it.

        portal_hiccup: the portal failed temporarily (network error, timeout, HTTP 5xx/429/403).
        A first read hit by that is tried again soon instead of failing the portal for hours.
        """
        portal = self.db.portal_get(portal_id)
        if portal is None:
            return
        if portal["state"] == states.INTEGRATING and not portal_hiccup:
            states.set_state(self.db, portal_id, states.FAILED, reason, run_id)
        elif portal["state"] == states.INTEGRATING:
            tries = self._failed_reads_before(portal_id, run_id) + 1
            if tries >= FIRST_READ_TRIES:
                states.set_state(self.db, portal_id, states.FAILED, f"{reason} (gave up after {tries} tries)", run_id)
            else:
                # Honest: nothing is delivered yet and we are still attaching.
                self.db.portal_update(
                    portal_id,
                    reason=f"{reason}; will try again in {min(INTEGRATING_RETRY_MINUTES, REFRESH_MINUTES):g} min "
                           f"(try {tries} of {FIRST_READ_TRIES})",
                )
                self.db.run_note(run_id, reason)
        elif portal["state"] == states.HEALING and after_repair:
            # The re-read after a repair crashed: the new recipe is unproven. The failed-retry
            # timer tries it again later with a plain read. Any other crashed read while healing
            # (e.g. resumed after a restart) keeps healing; the next timed read may repair.
            states.set_state(self.db, portal_id, states.FAILED, f"re-read after repair failed: {reason}", run_id)
        elif portal["state"] == states.FAILED:
            self.db.portal_update(portal_id, reason=f"still failing on retry: {reason}")
            self.db.run_note(run_id, reason)
        else:
            # Keep the state, but the reason must say the latest data may be stale.
            self.db.portal_update(portal_id, reason=f"{reason} (serving last good tenders)")
            self.db.run_note(run_id, reason)
