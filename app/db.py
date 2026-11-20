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
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    portal_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- read | repair
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,                       -- ok | failed | NULL while running
    notes TEXT NOT NULL DEFAULT '',
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
            "INSERT INTO runs (id, portal_id, kind, started_at) VALUES (?, ?, ?, ?)",
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
            "UPDATE runs SET finished_at = ?, result = ?, model_cost_usd = model_cost_usd + ? WHERE id = ?",
            (now_iso(), result, cost_usd, run_id),
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
        """The tenders the last healthy read actually saw (not older ones kept as closed)."""
        rows = self.all(
            "SELECT json FROM tenders WHERE portal_id = ? AND last_seen_at = "
            "(SELECT MAX(last_seen_at) FROM tenders WHERE portal_id = ?) ORDER BY id",
            (portal_id, portal_id),
        )
        return [json.loads(r["json"]) for r in rows]

    def save_tenders(self, portal_id: str, tenders: list[Tender]) -> int:
        """Store a healthy read. Changed tenders get a new lastChangedAt.

        Tenders that are no longer listed are kept and marked closed (plan section 9).
        Returns the number of tenders now stored for the portal.
        """
        now = now_iso()
        seen: set[str] = set()
        with self.transaction() as conn:
            for tender in tenders:
                if tender.id in seen:
                    continue
                seen.add(tender.id)
                data = tender.api_dict()
                self._upsert_tender(conn, portal_id, data, now)
            rows = conn.execute(
                "SELECT id, json FROM tenders WHERE portal_id = ? AND status != 'closed'", (portal_id,)
            ).fetchall()
            for row in rows:
                if row["id"] not in seen:
                    data = json.loads(row["json"])
                    data["status"] = "closed"
                    self._upsert_tender(conn, portal_id, data, now, seen_now=False)
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM tenders WHERE portal_id = ?", (portal_id,)
            ).fetchone()["n"]
            conn.execute("UPDATE portals SET tender_count = ? WHERE id = ?", (count, portal_id))
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
        """Files already stored for this tender from this link, in the order they were added."""
        return self.all(
            "SELECT * FROM documents WHERE portal_id = ? AND tender_id = ? AND source_url = ? ORDER BY rowid",
            (portal_id, tender_id, source_url),
        )

    # ----- model cost -----

    def llm_log(
        self, portal_id: str | None, run_id: str | None, model: str,
        tokens_in: int, tokens_out: int, cost_usd: float, purpose: str,
    ) -> None:
        self.execute(
            "INSERT INTO llm_calls (portal_id, run_id, model, tokens_in, tokens_out, cost_usd, purpose, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (portal_id, run_id, model, tokens_in, tokens_out, cost_usd, purpose, now_iso()),
        )

    def llm_total_cost(self) -> float:
        row = self.one("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls")
        return float(row["c"]) if row else 0.0

    def llm_cost_by_portal(self) -> dict[str, float]:
        rows = self.all("SELECT portal_id, SUM(cost_usd) AS c FROM llm_calls GROUP BY portal_id")
        return {r["portal_id"]: float(r["c"]) for r in rows}


db = DB()
