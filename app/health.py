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
import re
from datetime import datetime, timezone

from app.models import Health, RawTender, ReadResult, Tender


def clean_short_fields(rows: list[RawTender]) -> dict[str, int]:
    """Discard implausible short fields and retain the evidence for the health decision."""
    invalid = Counter()
    for row in rows:
        for field in ("title", "buyerName", "referenceNumber"):
            value = row.fields.get(field)
            text = " ".join(value) if isinstance(value, list) else value
            if text and (len(text) > 500 or re.search(r"<(?:script|style)\b|[.#][\w-]+\s*\{[^}]*:|\bfunction\s*\([^)]*\)\s*\{", text, re.I)):
                row.fields[field] = None
                invalid[field] += 1
    return dict(invalid)

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
        if new.verified_empty:
            return Health(ok=True)
        return _sick("no tenders found")

    problems: list[str] = []
    for field, count in new.invalid_fields.items():
        if count * 2 >= len(tenders):
            problems.append(f"{field} contained markup or implausible text on {count} of {len(tenders)} notices")
    previous = prev.tenders if prev else []
    problems += _identity_problems(new)
    problems += _fill_problems(tenders, previous)
    if new.deadline_times_seen and not any(t.submissionDeadline for t in tenders):
        problems.append("the source shows submission times, but no deadlines could be parsed with a time zone")
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
    document_problems = _document_problems(new)
    problems += document_problems
    if new.missing_open_files and not document_problems:
        problems.append(f"{new.missing_open_files} advertised file(s) for open tenders could not be delivered")
    return Health(ok=not problems, problems=problems, missing_document_ids=missing_document_ids)


def _sick(problem: str) -> Health:
    return Health(ok=False, problems=[problem])


def _identity_problems(new: ReadResult) -> list[str]:
    tenders = new.tenders
    problems = []
    if new.raw_count and len(tenders) < new.raw_count:
        problems.append(f"only {len(tenders)} of {new.raw_count} list entries had a readable tender id")
    # One id on two different notices: storage would silently drop one. A notice repeated
    # unchanged on overlapping pages is the same tender and stored once; its url may differ,
    # because list-only recipes use the listing page it was seen on.
    first: dict[str, dict] = {}
    conflicts = sorted({t.id for t in tenders
                        if first.setdefault(t.id, (data := t.model_dump(exclude={"url"}))) != data})
    if conflicts:
        problems.append(f"{len(conflicts)} tender ids belong to different notices (for example {conflicts[0]!r}), "
                        "so fields.id is not a unique notice id")
    if len(tenders) >= MIN_TENDERS_FOR_PATTERNS:
        distinct_ids = len({t.id for t in tenders})
        if distinct_ids < 0.9 * len(tenders):
            problems.append(f"tender ids are not unique ({distinct_ids} different ids for {len(tenders)} tenders)")
        titles = {t.title for t in tenders if t.title}
        if len(titles) == 1:
            problems.append("every tender has the same title, so the title is probably read from the wrong place")
    for field in ("title", "buyerName", "referenceNumber"):
        values = [getattr(t, field) for t in tenders if getattr(t, field)]
        invalid = [value for value in values if len(value) > 500 or re.search(
            r"[{}]|\.\w+__\w+|\bfunction\s*\(", value, re.I
        )]
        if invalid:
            problems.append(f"{_FIELD_NAMES.get(field, field)} contains markup or is too long")
    return problems


def _fill_rate(tenders: list[Tender], field: str) -> float:
    return sum(getattr(t, field) is not None for t in tenders) / len(tenders)


def _fill_problems(tenders: list[Tender], previous: list[Tender]) -> list[str]:
    problems = []
    # New notices can legitimately omit fields (for example a deadline with a date only).
    # Compare the same notices across reads. Title coverage still applies to the entire list.
    previous_by_id = {t.id: t for t in previous}
    continuing = [t for t in tenders if t.id in previous_by_id]
    before = [previous_by_id[t.id] for t in continuing]
    for field in REQUIRED_FIELDS:
        sample = tenders if field == "title" else continuing
        if not sample:
            continue
        rate = _fill_rate(sample, field)
        if rate >= MIN_FILL_RATE:
            continue
        used_to_work = bool(before) and _fill_rate(before, field) >= MIN_FILL_RATE
        if used_to_work or field == "title":
            missing = sum(getattr(t, field) is None for t in sample)
            problems.append(
                f"{_FIELD_NAMES[field]} is missing or unreadable for {missing} of {len(sample)} tenders"
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
    if failed and not any(tender.documents for tender in new.tenders):
        return [f"no documents delivered. {failed} of {tried} document downloads failed"]
    if (tried >= 2 and failed == tried) or (tried >= 4 and failed > MAX_DOCUMENT_FAILURE_SHARE * tried):
        return [f"{failed} of {tried} document downloads failed"]
    return []
