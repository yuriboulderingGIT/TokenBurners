"""State machine (P6). OWNER: A2."""

import pytest

from app import states
from app.db import DB


def test_state_changes_are_written_to_runs(tmp_path) -> None:
    db = DB(tmp_path / "s.sqlite")
    portal_id = db.portal_create("https://p.example/")
    run_id = db.run_create(portal_id, "read")

    states.set_state(db, portal_id, states.SERVING, "delivering 5 tenders", run_id)
    portal = db.portal_get(portal_id)
    assert portal["state"] == "serving" and portal["last_success_at"]
    assert "state integrating -> serving: delivering 5 tenders" in db.runs_for(portal_id)[0]["notes"]

    # Without a run id the change still lands in the portal's latest run.
    states.set_state(db, portal_id, states.FAILED, "repair gave up after: no tenders found")
    assert "state serving -> failed: repair gave up" in db.runs_for(portal_id)[0]["notes"]


def test_failed_always_has_a_reason_and_unknown_states_are_rejected(tmp_path) -> None:
    db = DB(tmp_path / "s.sqlite")
    portal_id = db.portal_create("https://p.example/")
    states.set_state(db, portal_id, states.FAILED, None)
    assert db.portal_get(portal_id)["reason"]
    with pytest.raises(ValueError):
        states.set_state(db, portal_id, "broken", "x")


def test_deleted_portal_is_left_alone(tmp_path) -> None:
    db = DB(tmp_path / "s.sqlite")
    portal_id = db.portal_create("https://p.example/")
    db.portal_delete(portal_id)
    states.set_state(db, portal_id, states.SERVING, "late result")
    row = db.one("SELECT state FROM portals WHERE id = ?", (portal_id,))
    assert row["state"] == "integrating"
