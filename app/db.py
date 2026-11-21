"""SQLite storage (plan section 4.3). OWNER: A1.

One file in DATA_DIR. Calls are synchronous and fast; a lock makes them safe to use
from the API and the worker at the same time.
"""

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import DB_PATH
from app.models import Recipe, Tender

SCHEMA = """
CREATE TABLE IF NOT EXISTS portals (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    tender_count INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    recipe_version INTEGER,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS recipes (
    portal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,          -- attach | repair
    model_cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (portal_id, version)
);
CREATE TABLE IF NOT EXISTS attach_notice_checkpoints (
    portal_id TEXT NOT NULL, fingerprint TEXT NOT NULL, raw TEXT NOT NULL, saved_at INTEGER NOT NULL,
    PRIMARY KEY (portal_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    portal_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- read | repair
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,                       -- ok | failed | NULL while running
    notes TEXT NOT NULL DEFAULT '',
    request_count INTEGER,
    model_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_by_portal ON runs (portal_id, started_at);
CREATE TABLE IF NOT EXISTS tenders (
    portal_id TEXT NOT NULL,
    id TEXT NOT NULL,
    json TEXT NOT NULL,
    hash TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (portal_id, id)
);
CREATE TABLE IF NOT EXISTS documents (
    file_id TEXT PRIMARY KEY,
    portal_id TEXT NOT NULL,
    tender_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    path_on_disk TEXT NOT NULL,
    source_url TEXT                    -- the link it came from (ZIP members share their archive's link)
);
CREATE TABLE IF NOT EXISTS document_sources (
    portal_id TEXT NOT NULL,
    tender_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_ids TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    validator_target TEXT NOT NULL,
    last_verified_at REAL NOT NULL,
    last_downloaded_at REAL NOT NULL,
    PRIMARY KEY (portal_id, tender_id, source_key)
);
CREATE TABLE IF NOT EXISTS read_documents (
    run_id TEXT NOT NULL,
    tender_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    PRIMARY KEY (run_id, tender_id, ordinal)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    portal_id TEXT,
    run_id TEXT,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    purpose TEXT NOT NULL,             -- attach | repair | check
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_reservations (
    id TEXT PRIMARY KEY,
    portal_id TEXT,
    run_id TEXT,
    max_cost_usd REAL NOT NULL,
    at TEXT NOT NULL
);
"""


def now_iso() -> str:
    """Current time as ISO 8601 with offset, e.g. 2026-11-20T09:00:00+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def close_if_past_deadline(tender: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The tender as clients see it. Stored tenders are only rewritten by a healthy read, so a
    portal that is healing or failed would keep serving `open` past the deadline. The change
    happened at the deadline, so that is its lastChangedAt (changedSince clients see it)."""
    deadline = tender.get("submissionDeadline")
    if tender["status"] != "open" or not deadline or datetime.fromisoformat(deadline) > now:
        return tender
    changed = max(tender["lastChangedAt"], deadline, key=datetime.fromisoformat)
    return tender | {"status": "closed", "lastChangedAt": changed}


class DB:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Bring databases created by older versions up to the current schema."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(documents)")}
        if "source_url" not in columns:
            self._conn.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS documents_by_source ON documents (portal_id, tender_id, source_url)"
        )

        tender_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(tenders)")}
        for column in ("missing_since", "source_status", "detail_url", "detail_checked_at"):
            if column not in tender_columns:
                self._conn.execute(f"ALTER TABLE tenders ADD COLUMN {column} TEXT")
        if "missing_since" not in tender_columns:
            self._conn.execute(
                "UPDATE tenders AS t SET missing_since = "
                "(SELECT MAX(last_seen_at) FROM tenders WHERE portal_id = t.portal_id) "
                "WHERE last_seen_at < (SELECT MAX(last_seen_at) FROM tenders WHERE portal_id = t.portal_id)"
            )
        portal_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(portals)")}
        if "monitoring_scope" not in portal_columns:
            self._conn.execute("ALTER TABLE portals ADD COLUMN monitoring_scope TEXT")
        if "request_count" not in {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}:
            self._conn.execute("ALTER TABLE runs ADD COLUMN request_count INTEGER")
        self._quarantine_legacy_categories()
        if "last_access_check" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(document_sources)")}:
            self._conn.execute("ALTER TABLE document_sources ADD COLUMN last_access_check REAL NOT NULL DEFAULT 0")
        self._quarantine_unverified_reads()

    def _quarantine_unverified_reads(self) -> None:
        """Older versions published first-read rows before checking document coverage."""
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS unverified_tenders AS SELECT * FROM tenders WHERE 0")
            portals = conn.execute(
                "SELECT id FROM portals WHERE last_success_at IS NULL AND tender_count > 0 "
                "AND EXISTS (SELECT 1 FROM runs WHERE portal_id=portals.id AND result='failed' "
                "AND instr(notes,'read ok, but no tender documents were found') > 0)"
            ).fetchall()
            for portal in portals:
                key = (portal["id"],)
                conn.execute("INSERT INTO unverified_tenders SELECT * FROM tenders WHERE portal_id=?", key)
                conn.execute("DELETE FROM tenders WHERE portal_id=?", key)
                conn.execute("UPDATE portals SET tender_count=0, reason=CASE WHEN reason LIKE 'no documents:%' "
                             "THEN 'Document delivery was never verified' ELSE reason END WHERE id=?", key)

    def _quarantine_legacy_categories(self) -> None:
        """Archive the false category rows identified in the November 2026 review."""
        if self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_category_rows'"):
            return
        with self.transaction() as conn:
            # Copy the complete original record. A subsequent real notice may use the same portal ID.
            conn.execute("CREATE TABLE IF NOT EXISTS legacy_category_rows AS SELECT * FROM tenders WHERE 0")
            affected: set[str] = set()
            rows = conn.execute(
                "SELECT portal_id, id, json, COUNT(*) OVER "
                "(PARTITION BY portal_id, json_extract(json, '$.url')) AS sharing_url FROM tenders "
                "WHERE json_valid(json) AND json_type(json, '$.url')='text' "
                "AND json_extract(json, '$.url') LIKE '%/categoryOverview.do%'"
            ).fetchall()
            for row in rows:
                data = json.loads(row["json"])
                if row["sharing_url"] != 1 or any(data.get(field) for field in (
                    "buyerName", "submissionDeadline", "publicationDate", "documents", "referenceNumber",
                )):
                    continue
                try:
                    url = urlsplit(data["url"])
                except ValueError:
                    continue
                if (url.scheme not in {"http", "https"} or not url.hostname or not url.query
                        or url.path.rsplit("/", 1)[-1].casefold() != "categoryoverview.do"):
                    continue
                key = row["portal_id"], row["id"]
                conn.execute("INSERT INTO legacy_category_rows SELECT * FROM tenders WHERE portal_id=? AND id=?", key)
                conn.execute("DELETE FROM tenders WHERE portal_id=? AND id=?", key)
                affected.add(row["portal_id"])
            for portal_id in affected:
                count = conn.execute("SELECT COUNT(*) FROM tenders WHERE portal_id=?", (portal_id,)).fetchone()[0]
                reason = (f"delivering {count} tenders after excluding historical category links" if count
                          else "the old listing contained only category links; refresh to repair the recipe")
                conn.execute(
                    "UPDATE portals SET tender_count=?, reason=CASE WHEN state='serving' THEN ? ELSE reason END, "
                    "state=CASE WHEN state='serving' AND ?=0 THEN 'failed' ELSE state END, "
                    "last_success_at=CASE WHEN ?=0 THEN NULL ELSE last_success_at END WHERE id=?",
                    (count, reason, count, count, portal_id),
                )

    # ----- low level -----

    def execute(self, sql: str, params: tuple | dict = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    # ----- portals -----

    def portal_get(self, portal_id: str) -> sqlite3.Row | None:
        """A live (not deleted) portal, or None."""
        return self.one("SELECT * FROM portals WHERE id = ? AND deleted_at IS NULL", (portal_id,))

    def portal_by_url(self, url: str) -> sqlite3.Row | None:
        """Any portal with this URL, deleted ones included."""
        return self.one("SELECT * FROM portals WHERE url = ?", (url,))

    def portal_list(self) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM portals WHERE deleted_at IS NULL ORDER BY created_at")

    def portal_create(self, url: str) -> str:
        portal_id = new_id()
        self.execute(
            "INSERT INTO portals (id, url, state, reason, created_at) VALUES (?, ?, 'integrating', ?, ?)",
            (portal_id, url, "attaching: learning how to read this portal", now_iso()),
        )
        return portal_id

    def portal_revive(self, portal_id: str) -> None:
        """Re-adding a deleted URL keeps its id and starts attaching again."""
        self.execute(
            "UPDATE portals SET deleted_at = NULL, state = 'integrating', reason = ? WHERE id = ?",
            ("attaching: learning how to read this portal", portal_id),
        )

    def portal_update(self, portal_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        self.execute(f"UPDATE portals SET {cols} WHERE id = :_id", {**fields, "_id": portal_id})

    def portal_delete(self, portal_id: str) -> None:
        self.portal_update(portal_id, deleted_at=now_iso())

    # ----- runs -----

    def run_create(self, portal_id: str, kind: str) -> str:
        run_id = new_id()
        self.execute(
            "INSERT INTO runs (id, portal_id, kind, started_at, request_count) VALUES (?, ?, ?, ?, 0)",
            (run_id, portal_id, kind, now_iso()),
        )
        return run_id

    def run_note(self, run_id: str, text: str) -> None:
        """Append one line to a run's notes: what the system decided and why."""
        self.execute(
            "UPDATE runs SET notes = notes || ? WHERE id = ?",
            (f"[{now_iso()}] {text}\n", run_id),
        )

    def run_finish(self, run_id: str, result: str, note: str | None = None, cost_usd: float = 0.0) -> None:
        if note:
            self.run_note(run_id, note)
        self.execute(
            "UPDATE runs SET finished_at = ?, result = ?, model_cost_usd = COALESCE("
            "(SELECT SUM(cost_usd) FROM llm_calls WHERE run_id = ?), model_cost_usd + ?) WHERE id = ?",
            (now_iso(), result, run_id, cost_usd, run_id),
        )

    def runs_close_orphans(self) -> None:
        """At startup: runs left 'running' by a stopped container did not finish."""
        self.execute(
            "UPDATE runs SET finished_at = ?, result = 'failed', notes = notes || ? WHERE result IS NULL",
            (now_iso(), f"[{now_iso()}] stopped: the container restarted during this run\n"),
        )

    def runs_for(self, portal_id: str, limit: int = 200) -> list[sqlite3.Row]:
        return self.all(
            "SELECT * FROM runs WHERE portal_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (portal_id, limit),
        )

    def runs_recent(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.all(
            "SELECT runs.*, portals.url, portals.deleted_at FROM runs JOIN portals ON portals.id = runs.portal_id "
            "ORDER BY runs.started_at DESC, runs.rowid DESC LIMIT ?",
            (limit,),
        )

    def runs_by_portal(self, each: int = 24) -> dict[str, list[sqlite3.Row]]:
        """portal_id -> its last `each` runs, oldest first (the dashboard's heartbeat strip)."""
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in self.all("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 2000"):
            runs = grouped.setdefault(row["portal_id"], [])
            if len(runs) < each:
                runs.append(row)
        return {pid: runs[::-1] for pid, runs in grouped.items()}

    def last_read_started(self, portal_id: str) -> str | None:
        row = self.one(
            "SELECT MAX(started_at) AS t FROM runs WHERE portal_id = ? AND kind = 'read'", (portal_id,)
        )
        return row["t"] if row else None

    # ----- recipes -----

    def recipe_latest(self, portal_id: str) -> Recipe | None:
        row = self.one(
            "SELECT json FROM recipes WHERE portal_id = ? ORDER BY version DESC LIMIT 1", (portal_id,)
        )
        return Recipe.model_validate_json(row["json"]) if row else None

    def recipe_save(self, portal_id: str, recipe: Recipe, created_by: str, cost_usd: float) -> int:
        with self.transaction() as conn:
            return self._insert_recipe(conn, portal_id, recipe, created_by, cost_usd)

    @staticmethod
    def _insert_recipe(
        conn: sqlite3.Connection, portal_id: str, recipe: Recipe, created_by: str, cost_usd: float,
    ) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM recipes WHERE portal_id = ?", (portal_id,)
        ).fetchone()
        version = row["v"] + 1
        recipe.version = version
        conn.execute(
            "INSERT INTO recipes (portal_id, version, json, created_at, created_by, model_cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (portal_id, version, recipe.to_json(), now_iso(), created_by, cost_usd),
        )
        conn.execute("UPDATE portals SET recipe_version = ? WHERE id = ?", (version, portal_id))
        return version

    def recipe_history(self) -> dict[str, list[sqlite3.Row]]:
        """portal_id -> its recipe versions, newest first (for the dashboard)."""
        history: dict[str, list[sqlite3.Row]] = {}
        for row in self.all(
            "SELECT portal_id, version, created_by, created_at FROM recipes ORDER BY version DESC"
        ):
            history.setdefault(row["portal_id"], []).append(row)
        return history

    # ----- tenders -----

    def tenders_for(self, portal_id: str) -> list[dict[str, Any]]:
        rows = self.all("SELECT json FROM tenders WHERE portal_id = ? ORDER BY id", (portal_id,))
        return [json.loads(r["json"]) for r in rows]

    def tenders_listed_last(self, portal_id: str) -> list[dict[str, Any]]:
        """Only rows present in the last healthy listing participate in the next health comparison."""
        rows = self.all("SELECT json FROM tenders WHERE portal_id = ? AND missing_since IS NULL ORDER BY id",
                        (portal_id,))
        return [json.loads(r["json"]) for r in rows]

    def unlisted_active_details(self, portal_id: str, listed: set[str]):
        return self.all(
            "SELECT id, detail_url FROM tenders WHERE portal_id = ? AND status = 'open' AND detail_url IS NOT NULL "
            "AND id NOT IN (SELECT value FROM json_each(?)) ORDER BY detail_checked_at, id",
            (portal_id, json.dumps(sorted(listed))),
        )

    def save_tenders(
        self, portal_id: str, tenders: list[Tender], *, scope: str | None = None,
        detail_urls: dict[str, str] | None = None, source_statuses: dict[str, str] | None = None,
        rechecks: dict[str, tuple[Tender, str | None] | None] | None = None,
        run_id: str | None = None, results_window: bool = False,
        repaired_recipe: Recipe | None = None, repair_cost_usd: float = 0.0,
    ) -> int:
        """Atomically publish a healthy listing. Notices it no longer lists are kept and marked closed,
        except in a latest-results window: there only closure evidence or the deadline closes them.
        Every absent notice records when it left the listing (missing_since)."""
        now = now_iso()
        seen: set[str] = set()
        with self.transaction() as conn:
            for tender in tenders:
                if tender.id in seen:
                    continue
                seen.add(tender.id)
                data = tender.api_dict()
                self._upsert_tender(conn, portal_id, data, now)
                if run_id is not None:
                    conn.executemany(
                        "INSERT INTO read_documents (run_id, tender_id, ordinal, file_id) VALUES (?, ?, ?, ?)",
                        [(run_id, tender.id, pos, doc.path.removeprefix("/files/"))
                         for pos, doc in enumerate(tender.documents)],
                    )
                conn.execute(
                    "UPDATE tenders SET missing_since = NULL, detail_checked_at = NULL, detail_url = ?, "
                    "source_status = ? WHERE portal_id = ? AND id = ?",
                    ((detail_urls or {}).get(tender.id), (source_statuses or {}).get(tender.id), portal_id, tender.id),
                )
            conn.execute(
                "UPDATE tenders SET missing_since = ? WHERE portal_id = ? AND missing_since IS NULL "
                "AND id NOT IN (SELECT value FROM json_each(?))", (now, portal_id, json.dumps(sorted(seen))),
            )
            # The brief: a tender is closed once the portal no longer lists it. A latest-results
            # window also drops notices that are still open, so there a notice keeps its status
            # until its own page says otherwise or its deadline passes (served closed then).
            rechecks = rechecks or {}
            missing = conn.execute(
                "SELECT id, json FROM tenders WHERE portal_id = ? AND missing_since IS NOT NULL AND status = 'open'",
                (portal_id,),
            ).fetchall()
            for row in missing:
                ident, data = row["id"], json.loads(row["json"])
                if ident in rechecks:
                    conn.execute("UPDATE tenders SET detail_checked_at = ? WHERE portal_id = ? AND id = ?",
                                 (now, portal_id, ident))
                if (observation := rechecks.get(ident)) is not None:
                    detail, raw_status = observation
                    if raw_status:
                        conn.execute("UPDATE tenders SET source_status = ? WHERE portal_id = ? AND id = ?",
                                     (raw_status, portal_id, ident))
                    if detail.submissionDeadline:
                        data["submissionDeadline"] = detail.submissionDeadline
                    if detail.status in {"closed", "withdrawn"}:
                        data["status"] = detail.status
                if data["status"] == "open" and not results_window:
                    data["status"] = "closed"
                self._upsert_tender(conn, portal_id, data, now, seen_now=False)
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM tenders WHERE portal_id = ?", (portal_id,)
            ).fetchone()["n"]
            conn.execute("UPDATE portals SET tender_count = ?, monitoring_scope = COALESCE(?, monitoring_scope) "
                         "WHERE id = ?", (count, scope, portal_id))
            if repaired_recipe is not None:
                self._insert_recipe(conn, portal_id, repaired_recipe, "repair", repair_cost_usd)
        return count

    @staticmethod
    def _upsert_tender(
        conn: sqlite3.Connection, portal_id: str, data: dict[str, Any], now: str, seen_now: bool = True
    ) -> None:
        data = dict(data)
        data.pop("lastChangedAt", None)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        old = conn.execute(
            "SELECT json, hash, last_changed_at, last_seen_at FROM tenders WHERE portal_id = ? AND id = ?",
            (portal_id, data["id"]),
        ).fetchone()
        changed = old is None or old["hash"] != digest
        last_changed = now if changed else old["last_changed_at"]
        if changed and old is not None:
            # Already served as closed at its deadline: same data, so not a second change.
            served = close_if_past_deadline(json.loads(old["json"]), datetime.fromisoformat(now))
            if {k: v for k, v in served.items() if k != "lastChangedAt"} == data:
                last_changed = served["lastChangedAt"]
        last_seen = now if seen_now or old is None else old["last_seen_at"]
        data["lastChangedAt"] = last_changed
        conn.execute(
            "INSERT INTO tenders (portal_id, id, json, hash, last_changed_at, last_seen_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (portal_id, id) DO UPDATE SET json = excluded.json, hash = excluded.hash, "
            "last_changed_at = excluded.last_changed_at, last_seen_at = excluded.last_seen_at, "
            "status = excluded.status",
            (portal_id, data["id"], json.dumps(data), digest, last_changed, last_seen, data["status"]),
        )

    # ----- documents -----

    def document_add(
        self, portal_id: str, tender_id: str, file_name: str, size_bytes: int, sha256: str,
        path_on_disk: str, source_url: str | None = None, file_id: str | None = None,
    ) -> str:
        file_id = file_id or new_id()
        self.execute(
            "INSERT INTO documents (file_id, portal_id, tender_id, file_name, size_bytes, sha256, "
            "path_on_disk, source_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, portal_id, tender_id, file_name, size_bytes, sha256, path_on_disk, source_url),
        )
        return file_id

    def document_get(self, file_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM documents WHERE file_id = ?", (file_id,))

    def documents_from_source(self, portal_id: str, tender_id: str, source_url: str) -> list[sqlite3.Row]:
        """All revisions from this source, including files from before validator tracking."""
        return self.all(
            "SELECT * FROM documents WHERE portal_id = ? AND tender_id = ? AND source_url = ? ORDER BY rowid",
            (portal_id, tender_id, source_url),
        )

    def source_files(self, file_ids: str) -> list[sqlite3.Row]:
        return self.all(
            "SELECT d.* FROM json_each(?) ids JOIN documents d ON d.file_id = ids.value ORDER BY ids.key",
            (file_ids,),
        )

    # ----- model cost -----

    def llm_try_reserve(
        self, portal_id: str, run_id: str | None, max_cost: float, budget: float,
    ) -> str | None:
        reservation = uuid.uuid4().hex
        # The balance check and insert share one SQLite write, including across workers.
        with self.transaction() as conn:
            inserted = conn.execute(
                "INSERT INTO llm_reservations (id, portal_id, run_id, max_cost_usd, at) "
                "SELECT ?, ?, ?, ?, ? WHERE ? + "
                "(SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls) + "
                "(SELECT COALESCE(SUM(max_cost_usd), 0) FROM llm_reservations) <= ?",
                (reservation, portal_id, run_id, max_cost, now_iso(), max_cost, budget),
            )
            return reservation if inserted.rowcount else None

    def llm_release(self, reservation: str) -> None:
        self.execute("DELETE FROM llm_reservations WHERE id = ?", (reservation,))

    def llm_log(
        self, portal_id: str | None, run_id: str | None, model: str,
        tokens_in: int, tokens_out: int, cost_usd: float, purpose: str,
        *, reservation: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_calls (portal_id, run_id, model, tokens_in, tokens_out, cost_usd, purpose, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (portal_id, run_id, model, tokens_in, tokens_out, cost_usd, purpose, now_iso()),
            )
            if run_id is not None:
                conn.execute("UPDATE runs SET model_cost_usd=model_cost_usd+? WHERE id=?", (cost_usd, run_id))
            if reservation is not None:
                conn.execute("DELETE FROM llm_reservations WHERE id = ?", (reservation,))

    def llm_total_cost(self) -> float:
        row = self.one("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls")
        return float(row["c"]) if row else 0.0

    def llm_cost_by_portal(self) -> dict[str, float]:
        rows = self.all("SELECT portal_id, SUM(cost_usd) AS c FROM llm_calls GROUP BY portal_id")
        return {r["portal_id"]: float(r["c"]) for r in rows}


db = DB()
