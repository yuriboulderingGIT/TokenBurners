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
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app import states
from app.config import (
    DOC_REVALIDATIONS_PER_READ, FAILED_RETRY_MINUTES, FIRST_READ_TRIES, INTEGRATING_RETRY_MINUTES,
    REFRESH_MINUTES, REFRESH_REPAIR_MINUTES, WORKER_CONCURRENCY,
)
from app.db import DB
from app.documents import store_documents
from app.fetcher import Fetcher, PortalCancelled, PortalFetcher, RunPageCache
from app.health import check_health, clean_short_fields
from app.models import Health, RawTender, ReadResult, Recipe, Tender
from brain.blocks import Blocked
from brain.learn import AttachFailed, StoredTenderIds, TemporaryAttachFailure, learn_recipe, validate_candidate
from brain.lifecycle import recheck_missing
from brain.llm import LLM, BudgetExceeded, LLMUnavailable
from brain.normalize import _single_text, normalize, shows_deadline_time
from brain.repair import repair
from brain.scope import is_results_window, monitoring_scope, preserves_scope, scope_known
from brain.run_recipe import PageLimitExceeded, PaginationError, RecipeReadError, run_recipe

log = logging.getLogger("watchtower.worker")
_TEMPORARY_ATTACH = "could not attach (temporary portal error): "


def _documents_unverified(recipe: Recipe, tenders: list[Tender]) -> bool:
    """A read without document links, that never looked where the documents usually are.

    Documents live on a tender's own page. If the recipe opened each page (detail.link) and found
    no file, the listing is document-free by evidence. If the tenders have own pages the recipe
    never opened, or several tenders share one address (a preview without notice pages), the
    documents were simply not reached.
    """
    if recipe.detail.link is not None:
        return False
    urls = [tender.url for tender in tenders]
    return len(set(urls)) < len(urls) or any(url != recipe.list_.url for url in urls)


class Worker:
    def __init__(self, db: DB, fetcher: Fetcher, llm: LLM) -> None:
        self.db = db
        self.fetcher = fetcher
        self.llm = llm
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.active: dict[str, str] = {}  # portal_id -> run_id of the queued or running read
        self._tasks: list[asyncio.Task] = []
        if self.fetcher is not None:
            self.fetcher.on_request = self._record_request
        self._queued_actions: dict[str, str] = {}  # consumed at start; an in-flight request keeps its run id

    # ----- control -----

    def _record_request(self, portal_id: str) -> None:
        if run_id := self.active.get(portal_id):
            self.db.execute("UPDATE runs SET request_count=request_count+1 WHERE id=?", (run_id,))

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

    def request_read(self, portal_id: str, *, action: str = "scheduled") -> str:
        """Queue a read unless one is already queued or running; return its run id."""
        if portal_id in self.active:
            active = self.active[portal_id]
            if self._queued_actions.get(active) in {"scheduled", "refresh"} and action != "scheduled":
                self._queued_actions[active] = action
            return active
        run_id = self.db.run_create(portal_id, "read")
        self.active[portal_id] = run_id
        self._queued_actions[run_id] = action
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
        """A scheduled retry stays model-free for failed saved recipes; operators can request recovery."""
        action = self._queued_actions.pop(run_id, "scheduled")
        try:
            portal = self.db.portal_get(portal_id)
            retry = portal is not None and portal["state"] == states.FAILED
            if portal is not None and portal["recipe_version"] is not None and action in {"repair", "relearn"}:
                self.db.execute("UPDATE runs SET kind = 'repair' WHERE id = ?", (run_id,))
                recipe = self.db.recipe_latest(portal_id)
                await self._repair(portal_id, recipe, Health(ok=False, problems=[f"operator requested {action}"]),
                                   self.fetcher.for_portal(portal_id), portal_url=portal["url"],
                                   repair_run=run_id, relearn=action == "relearn")
                return
            if (retry and action == "refresh" and portal["recipe_version"] is None
                    and not self._refresh_may_use_model(portal_id, run_id, "read")):
                self.db.run_finish(run_id, "failed", "refresh of a portal that could not attach: the last attempt "
                                                     f"ran in the last {REFRESH_REPAIR_MINUTES:.0f} minutes")
                return
            allow_repair = not retry or action == "relearn" or action == "repair" or (
                action == "refresh" and self._refresh_may_use_model(portal_id, run_id, "repair"))
            if retry and not allow_repair and portal["recipe_version"] is not None:
                self.db.run_note(run_id, "retry of a failed portal: plain read, no repair" if action == "scheduled"
                                 else f"refresh of a failed portal: plain read, a repair ran in the last "
                                      f"{REFRESH_REPAIR_MINUTES:.0f} minutes")
            await self._read(portal_id, run_id, allow_repair=allow_repair, retry=retry)
        except Blocked as exc:
            states.set_state(self.db, portal_id, states.FAILED, f"blocked: {exc}", run_id)
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
        fetch = RunPageCache(self.fetcher, portal_id) if recipe is None or not portal["last_success_at"] else self.fetcher.for_portal(portal_id)
        def checkpoint(message):
            self.db.run_note(run_id, message)
            current = self.db.portal_get(portal_id)
            if current and not current["last_success_at"] and current["state"] == states.INTEGRATING:
                self.db.portal_update(portal_id, reason=message)
        fetch.progress = checkpoint
        if not portal["last_success_at"] and isinstance(fetch, RunPageCache):
            self.db.execute("DELETE FROM attach_notice_checkpoints WHERE saved_at < unixepoch()-900")
            def staged_notice(key):
                row = self.db.one("SELECT raw FROM attach_notice_checkpoints WHERE portal_id=? AND fingerprint=? "
                                  "AND saved_at >= unixepoch()-900", (portal_id, key))
                return RawTender.model_validate_json(row["raw"]) if row else None
            def stage_notice(key, raw):
                self.db.execute("INSERT OR REPLACE INTO attach_notice_checkpoints VALUES (?,?,?,unixepoch())",
                                (portal_id, key, raw.model_dump_json()))
            fetch.staged_notice, fetch.stage_notice = staged_notice, stage_notice
        cost = 0.0
        try:
            if recipe is None:
                if retry:
                    states.set_state(self.db, portal_id, states.INTEGRATING, "retrying attach: locating the tender listing", run_id)
                self.db.run_note(run_id, "attach: no recipe yet, asking the model to learn one")
                llm = self.llm.scoped(portal_id, run_id, "reattach" if retry else "attach")
                recipe = await learn_recipe(portal["url"], fetch, llm)
                cost += llm.total_cost
                version = self.db.recipe_save(portal_id, recipe, "attach", llm.total_cost)
                self.db.run_note(run_id, f"recipe v{version} learned by {recipe.learned_by} (${llm.total_cost:.4f})")

            if portal["state"] == states.INTEGRATING or portal["recipe_version"] is None:
                states.set_state(self.db, portal_id, states.INTEGRATING,
                                 "recipe ready, reading tenders and documents for the first time", run_id)

            pagination_page = None
            too_long = False
            try:
                result = await self._execute(portal_id, recipe, fetch)
            except RecipeReadError as exc:
                health = Health(ok=False, problems=[str(exc)])
                too_long = isinstance(exc, PageLimitExceeded)
                if isinstance(exc, PaginationError):
                    pagination_page = exc.page_sample
            else:
                health = check_health(self._previous(portal_id), result)
                self.db.run_note(run_id, f"read {result.raw_count} list entries, {len(result.tenders)} tenders")
                if health.ok:
                    files = sum(len(tender.documents) for tender in result.tenders)
                    if (result.tenders and not files and not result.tender_ids_with_document_links
                            and _documents_unverified(recipe, result.tenders)):
                        reason = (f"no documents: found {len(result.tenders)} tender(s), but could not verify "
                                  "their documents. This read was not published")
                        states.set_state(self.db, portal_id, states.FAILED, reason, run_id)
                        self.db.run_finish(run_id, "failed", reason, cost)
                        return False
                    listed = {tender.id for tender in result.tenders}
                    rechecks = await recheck_missing(self.db, portal_id, recipe, listed, fetch)
                    scope = monitoring_scope(portal["url"], recipe)
                    if result.documents_kept:
                        self.db.run_note(run_id, f"{result.documents_kept} known document(s) could not be checked "
                                                 "again; serving their last verified files")
                    count = self.db.save_tenders(
                        portal_id, result.tenders, run_id=run_id, scope=scope, rechecks=rechecks,
                        results_window=is_results_window(scope),
                        detail_urls=result.detail_urls, source_statuses=result.source_statuses,
                        repaired_recipe=repaired_recipe, repair_cost_usd=repair_cost_usd,
                    )
                    reason = f"delivering {count} tenders"
                    if files:
                        file_label = "document" if files == 1 else "documents"
                        if result.cached_files or result.documents_kept:
                            reason += f". {files} {file_label} available, {result.cached_files} from cache"
                        else:
                            reason += f". Latest read verified {files} {file_label}"
                    elif result.documents_tried:
                        # Files over 250 MB may be left out (track rules); failed downloads fail health.
                        reason += ". No documents delivered within the file limit"
                    else:
                        reason += ". No document links found"  # the tender pages were read and show no files
                    if scope_known(scope):
                        reason += f". Scope: {scope}"
                    if result.access_probes:
                        self.db.run_note(run_id, f"Checked access to {result.access_probes} cached document source; "
                                                f"{result.cached_files} files served from verified storage")
                    if count > len(listed):
                        reason += f". {len(listed)} listed now, {count - len(listed)} kept from earlier reads"
                    states.set_state(self.db, portal_id, states.SERVING, reason, run_id)
                    self.db.execute("DELETE FROM attach_notice_checkpoints WHERE portal_id=?", (portal_id,))
                    self.db.run_finish(run_id, "ok", cost_usd=cost)
                    return True

            problems = "; ".join(health.problems)
            self.db.run_finish(run_id, "failed", f"health check failed: {problems}", cost)
            if too_long:
                # The list outgrew the page limit. A new recipe cannot change its length, so no paid
                # repair; the last correct tenders stay available and the reason says what to do.
                states.set_state(self.db, portal_id, states.FAILED, problems, run_id)
            elif allow_repair:
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
        invalid_fields = clean_short_fields(raws)
        tenders: list[Tender] = []
        tender_ids_with_document_links: set[str] = set()
        detail_urls: dict[str, str] = {}
        source_statuses: dict[str, str] = {}
        tried = failed = blocked = failed_revalidations = cached = missing_open = probes = 0
        block_kind: str | None = None
        # Known files are checked again stalest first, a bounded number per read.
        revalidate = {row["source_key"] for row in self.db.all(
            "SELECT source_key FROM document_sources WHERE portal_id = ? ORDER BY last_verified_at LIMIT ?",
            (portal_id, DOC_REVALIDATIONS_PER_READ),
        )}
        observed = {hashlib.sha256(url.encode()).hexdigest() for raw in raws for url in raw.document_links}
        fetch.probe_source = next((row["source_key"] for row in self.db.all(
            "SELECT source_key FROM document_sources WHERE portal_id=? ORDER BY MAX(last_access_check,last_verified_at),source_key",
            (portal_id,)) if row["source_key"] in observed), None)
        normalized = [(raw, tender) for raw in raws if (tender := normalize(raw, recipe.locale)) is not None]
        normalized.sort(key=lambda pair: pair[1].status != "open")
        for pos, (raw, tender) in enumerate(normalized):
            if raw.archived_summary:
                previous = self.db.one("SELECT json FROM tenders WHERE portal_id=? AND id=?", (portal_id, tender.id))
                if previous:
                    old = Tender.model_validate_json(previous["json"])
                    tender = old.model_copy(update={"status": tender.status, "submissionDeadline": tender.submissionDeadline or old.submissionDeadline})
                    cached += len(tender.documents)
                    if tender.documents:
                        tender_ids_with_document_links.add(tender.id)
            if pos and pos % 10 == 0 and getattr(fetch, "progress", None):
                fetch.progress(f"Checking documents: {pos} of {len(normalized)} notices processed")
            if raw.detail_url:
                detail_urls[tender.id] = raw.detail_url
            if status := _single_text(raw.fields.get("status")):
                source_statuses[tender.id] = status
            if raw.document_links:
                tender_ids_with_document_links.add(tender.id)
                links = list(dict.fromkeys(raw.document_links))
                expected_names = {"expected_names": raw.document_names} if raw.document_names else {}
                stored = await store_documents(self.db, portal_id, tender, links, fetch, revalidate=revalidate, **expected_names)
                tender.documents = stored.documents
                tried += len(links)
                failed += stored.failed + stored.blocked
                failed_revalidations += stored.failed_revalidations
                cached += stored.cached_files
                probes += stored.access_probes
                if tender.status == "open":
                    missing_open += stored.failed + stored.blocked
                blocked += stored.blocked
                block_kind = block_kind or stored.block_kind
            tenders.append(tender)
        if tried and blocked == tried:
            # Every document sits behind a login or captcha: the portal can't be delivered.
            raise Blocked(block_kind or "login", "the tender documents can't be downloaded without it")
        return ReadResult(
            tenders=tenders, raw_count=len(raws), documents_tried=tried, documents_failed=failed,
            deadline_times_seen=sum(shows_deadline_time(raw) for raw in raws),
            documents_kept=failed_revalidations, cached_files=cached, access_probes=probes,
            invalid_fields=invalid_fields, missing_open_files=missing_open,
            tender_ids_with_document_links=tender_ids_with_document_links,
            detail_urls=detail_urls, source_statuses=source_statuses,
        )

    async def _repair(
        self, portal_id: str, recipe: Recipe, health: Health, fetch: PortalFetcher, *,
        portal_url: str,
        pagination_page: dict[str, str] | None = None,
        repair_run: str | None = None, relearn: bool = False,
    ) -> None:
        problems = "; ".join(health.problems)
        repair_run = repair_run or self.db.run_create(portal_id, "repair")
        self.active[portal_id] = repair_run  # a refresh now returns the run that is still going
        states.set_state(self.db, portal_id, states.HEALING, f"repairing after: {problems}", repair_run)
        llm = self.llm.scoped(portal_id, repair_run, "repair")
        try:
            previous = self._previous(portal_id)
            if relearn:
                new_recipe = await learn_recipe(portal_url, fetch, llm)
                if previous:
                    await validate_candidate(new_recipe, fetch, stored_ids=StoredTenderIds.from_tenders(previous.tenders, recipe))
            else:
                new_recipe = await repair(
                    recipe, health, fetch, llm, portal_url=portal_url, pagination_page=pagination_page,
                    stored_tenders=previous.tenders if previous else None,
                )
            if new_recipe is not None and not preserves_scope(portal_url, recipe, new_recipe):
                self.db.run_finish(repair_run, "failed", "replacement changed the monitored scope", llm.total_cost)
                states.set_state(self.db, portal_id, states.FAILED, "replacement changed the monitored scope")
                return
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

    def _refresh_may_use_model(self, portal_id: str, run_id: str, kind: str) -> bool:
        """A manual refresh of a failed portal may call the model, for a repair or a new attach, at most
        every REFRESH_REPAIR_MINUTES. Clients may call refresh every few minutes."""
        last = self.db.one("SELECT MAX(started_at) AS at FROM runs WHERE portal_id = ? AND kind = ? AND id != ?",
                           (portal_id, kind, run_id))
        if last is None or last["at"] is None:
            return True
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])
        return elapsed >= timedelta(minutes=REFRESH_REPAIR_MINUTES)

    def _previous(self, portal_id: str) -> ReadResult | None:
        """What the last healthy read saw. Retained notices outside the listing are tracked separately."""
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
