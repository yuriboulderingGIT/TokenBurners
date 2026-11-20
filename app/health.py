"""Health checks after every read. OWNER: A2.

A read may only lead to `serving` if this returns ok. When unsure, report a problem:
an honest `failed` scores better than `serving` with bad data. Problems are written in plain
words because they become the portal's reason on the dashboard.

`prev` holds the tenders the last healthy read saw (None before the first one). Checks that
compare with it only judge what changed, so a portal that never shows a field is not punished
for it, while a field that suddenly stops being found is.

Checks:
- no tenders, or list entries without a tender id
- tender ids not unique, or every tender with the same title (a selector hit a page header)
- a required field that used to be found for >= 80% of tenders now found for < 80%
  (titles must always be found for >= 80%)
- the tender count fell by more than half, not counting tenders that closed on time
- the same tender page now yields a different id
- document links disappearing from a majority of continuing tenders that had files and kept their status,
  or widespread document download failures
"""

from collections import Counter
from datetime import datetime, timezone

from app.models import Health, ReadResult, Tender

REQUIRED_FIELDS = ("title", "buyerName", "publicationDate", "submissionDeadline")
MIN_FILL_RATE = 0.8
MIN_ID_RATE = 0.8
MAX_DROP = 0.5
MIN_TENDERS_FOR_DROP = 4  # smaller portals swing too much on their own
MIN_TENDERS_FOR_PATTERNS = 3
MAX_CHANGED_ID_SHARE = 0.2
MAX_DOCUMENT_FAILURE_SHARE = 0.5
MAX_DOCUMENT_LINK_LOSS_SHARE = 0.5

_FIELD_NAMES = {
    "title": "the title",
    "buyerName": "the buyer name",
    "publicationDate": "the publication date",
    "submissionDeadline": "the submission deadline",
}


def check_health(prev: ReadResult | None, new: ReadResult) -> Health:
    tenders = new.tenders
    if not tenders:
        if new.raw_count:
            return _sick(f"the list showed {new.raw_count} entries, but none had a readable tender id")
        return _sick("no tenders found")

    problems: list[str] = []
    previous = prev.tenders if prev else []
    problems += _identity_problems(new)
    problems += _fill_problems(tenders, previous)
    missing_document_ids: set[str] = set()
    if previous:
        problems += _drop_problems(tenders, previous)
        problems += _changed_id_problems(tenders, previous)
        documented = {t.id: t.status for t in previous if t.documents}
        comparable = {t.id for t in tenders if documented.get(t.id) == t.status}
        linked = new.tender_ids_with_document_links
        if linked is None:
            linked = {t.id for t in tenders if t.documents}
        missing = comparable - linked
        if len(missing) > MAX_DOCUMENT_LINK_LOSS_SHARE * len(comparable):
            missing_document_ids = missing
            problems.append(
                f"document links disappeared for {len(missing)} of {len(comparable)} continuing "
                "tenders that previously had documents. Check the documents.link selector"
            )
    problems += _document_problems(new)
    return Health(ok=not problems, problems=problems, missing_document_ids=missing_document_ids)


def _sick(problem: str) -> Health:
    return Health(ok=False, problems=[problem])


def _identity_problems(new: ReadResult) -> list[str]:
    tenders = new.tenders
    problems = []
    if new.raw_count and len(tenders) < MIN_ID_RATE * new.raw_count:
        problems.append(f"only {len(tenders)} of {new.raw_count} list entries had a readable tender id")
    if len(tenders) >= MIN_TENDERS_FOR_PATTERNS:
        distinct_ids = len({t.id for t in tenders})
        if distinct_ids < 0.9 * len(tenders):
            problems.append(f"tender ids are not unique ({distinct_ids} different ids for {len(tenders)} tenders)")
        titles = {t.title for t in tenders if t.title}
        if len(titles) == 1:
            problems.append("every tender has the same title, so the title is probably read from the wrong place")
    return problems


def _fill_rate(tenders: list[Tender], field: str) -> float:
    return sum(getattr(t, field) is not None for t in tenders) / len(tenders)


def _fill_problems(tenders: list[Tender], previous: list[Tender]) -> list[str]:
    problems = []
    for field in REQUIRED_FIELDS:
        rate = _fill_rate(tenders, field)
        if rate >= MIN_FILL_RATE:
            continue
        used_to_work = bool(previous) and _fill_rate(previous, field) >= MIN_FILL_RATE
        if used_to_work or field == "title":
            missing = sum(getattr(t, field) is None for t in tenders)
            problems.append(
                f"{_FIELD_NAMES[field]} is missing or unreadable for {missing} of {len(tenders)} tenders"
            )
    return problems


def _drop_problems(tenders: list[Tender], previous: list[Tender]) -> list[str]:
    """Tenders that closed on time may disappear; others vanishing in bulk means a broken read."""
    now = datetime.now(timezone.utc)
    current = {t.id for t in tenders}
    expected = [t for t in previous if t.id in current or not _closed_naturally(t, now)]
    if len(expected) >= MIN_TENDERS_FOR_DROP and len(current) < (1 - MAX_DROP) * len(expected):
        return [f"the tender count dropped from {len(expected)} to {len(current)}"]
    return []


def _closed_naturally(tender: Tender, now: datetime) -> bool:
    if tender.status != "open":
        return True
    if tender.submissionDeadline:
        try:
            return datetime.fromisoformat(tender.submissionDeadline) <= now
        except ValueError:
            return False
    return False


def _unique_by_url(tenders: list[Tender]) -> dict[str, str]:
    """url -> id, only for URLs that belong to exactly one tender (list-only portals share one URL)."""
    counts = Counter(t.url for t in tenders)
    return {t.url: t.id for t in tenders if counts[t.url] == 1}


def _changed_id_problems(tenders: list[Tender], previous: list[Tender]) -> list[str]:
    before, now = _unique_by_url(previous), _unique_by_url(tenders)
    common = before.keys() & now.keys()
    changed = [url for url in common if before[url] != now[url]]
    if changed and len(changed) >= MAX_CHANGED_ID_SHARE * len(common):
        return [f"{len(changed)} tenders now show a different id on the same page than before"]
    return []


def _document_problems(new: ReadResult) -> list[str]:
    tried, failed = new.documents_tried, new.documents_failed
    if (tried >= 2 and failed == tried) or (tried >= 4 and failed > MAX_DOCUMENT_FAILURE_SHARE * tried):
        return [f"{failed} of {tried} document downloads failed"]
    return []
