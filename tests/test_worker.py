"""Worker rules. OWNER: A1."""

from app.db import db
from app.worker import Worker


def test_refresh_while_read_is_queued_returns_same_run_id() -> None:
    worker = Worker(db, fetcher=None, llm=None)  # not started: reads stay queued
    portal_id = db.portal_create("https://portal.example/worker-test")
    first = worker.request_read(portal_id)
    assert worker.request_read(portal_id) == first
    assert worker.queue.qsize() == 1
