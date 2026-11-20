"""Portal state machine. OWNER: A2.

integrating -> serving | failed
serving     -> healing (checks failed) -> serving (repair worked) | failed (gave up)
any         -> failed  (login / captcha block)
failed      -> serving (a later plain retry read passed the health checks)

Rules:
- `serving` only after a read passed the health checks (the worker guarantees this).
- While healing, the API keeps serving the last correct tenders: the worker only stores
  tenders from a healthy read, so this holds automatically.
- `failed` always comes with a reason in plain words.
- Every state change is written into a run's notes (the given run, else the portal's latest),
  so the dashboard and the video can show what the system decided and why.
"""

from app.db import DB, now_iso

INTEGRATING = "integrating"
SERVING = "serving"
HEALING = "healing"
FAILED = "failed"
STATES = (INTEGRATING, SERVING, HEALING, FAILED)


def set_state(db: DB, portal_id: str, state: str, reason: str | None, run_id: str | None = None) -> None:
    if state not in STATES:
        raise ValueError(f"unknown portal state: {state}")
    if state == FAILED and not reason:
        reason = "failed for an unknown reason"
    portal = db.portal_get(portal_id)
    if portal is None:
        return  # deleted meanwhile: nothing to report on
    fields: dict = {"state": state, "reason": reason}
    if state == SERVING:
        fields["last_success_at"] = now_iso()
    db.portal_update(portal_id, **fields)

    target_run = run_id or _latest_run(db, portal_id)
    if target_run:
        change = f"state {portal['state']} -> {state}" if portal["state"] != state else f"state stays {state}"
        db.run_note(target_run, change + (f": {reason}" if reason else ""))


def _latest_run(db: DB, portal_id: str) -> str | None:
    runs = db.runs_for(portal_id, limit=1)
    return runs[0]["id"] if runs else None
