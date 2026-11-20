"""Health checks after every read. OWNER: A2.

A read may only lead to `serving` if this returns ok. When unsure, report a problem:
an honest `failed` scores better than `serving` with bad data.
"""

from app.models import Health, ReadResult


def check_health(prev: ReadResult | None, new: ReadResult) -> Health:
    problems: list[str] = []
    if not new.tenders:
        problems.append("no tenders found")
    # TODO(A2, P9):
    # - tender count dropped by more than 50% compared to prev
    # - required-field fill rate below 80% (title, buyerName, publicationDate, submissionDeadline)
    # - dates that don't parse
    # - ids changed for the same tenders (same url, different id)
    # - document downloads failing (new.documents_failed / new.documents_tried)
    return Health(ok=not problems, problems=problems)
