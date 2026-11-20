"""Portal state machine. OWNER: A2.

integrating -> serving | failed
serving     -> healing (checks failed) -> serving (repair worked) | failed (gave up)
any         -> failed  (login / captcha block)

Rule: while healing, the API keeps serving the last correct tenders (the worker only
stores tenders from a healthy read, so this holds automatically).
Every state change is written into the run's notes, so the dashboard can show it.
"""

from app.db import DB, now_iso

INTEGRATING = "integrating"
SERVING = "serving"
HEALING = "healing"
FAILED = "failed"


def set_state(db: DB, portal_id: str, state: str, reason: str | None, run_id: str | None = None) -> None:
    fields: dict = {"state": state, "reason": reason}
    if state == SERVING:
        fields["last_success_at"] = now_iso()
    db.portal_update(portal_id, **fields)
    if run_id:
        db.run_note(run_id, f"state -> {state}" + (f": {reason}" if reason else ""))
    # TODO(A2): sanity rules, e.g. never go to serving with 0 tenders; reasons in plain words.
