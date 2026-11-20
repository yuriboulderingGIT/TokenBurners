"""Health checks (P9). OWNER: A2."""

from app.health import check_health
from app.models import Document, ReadResult, Tender

FUTURE = "2099-01-01T10:00:00+01:00"
PAST = "2020-01-01T10:00:00+01:00"


def tender(n: int, **overrides) -> Tender:
    data = dict(
        id=f"T-{n}", url=f"https://p.example/t/{n}", title=f"Tender {n}", buyerName="City",
        publicationDate="2026-11-20", submissionDeadline=FUTURE, status="open",
    )
    data.update(overrides)
    return Tender(**data)


def read(tenders: list[Tender], raw_count: int | None = None, **kw) -> ReadResult:
    return ReadResult(tenders=tenders, raw_count=len(tenders) if raw_count is None else raw_count, **kw)


GOOD = read([tender(n) for n in range(1, 11)])


def test_good_read_is_healthy_first_time_and_after() -> None:
    assert check_health(None, GOOD).ok
    assert check_health(GOOD, GOOD).ok


def test_no_tenders_and_entries_without_ids() -> None:
    assert check_health(GOOD, read([])).problems == ["no tenders found"]
    problems = check_health(GOOD, read([], raw_count=7)).problems
    assert "none had a readable tender id" in problems[0]
    partly = check_health(None, read([tender(1), tender(2)], raw_count=5))
    assert not partly.ok and "only 2 of 5" in partly.problems[0]


def test_duplicate_ids_and_identical_titles_are_suspicious() -> None:
    same_id = check_health(None, read([tender(1, id="X") for _ in range(4)] + [tender(9)]))
    assert any("not unique" in p for p in same_id.problems)
    same_title = check_health(None, read([tender(n, title="Home") for n in range(1, 5)]))
    assert any("same title" in p for p in same_title.problems)


def test_field_that_stopped_being_found_is_a_problem() -> None:
    new = read([tender(n, submissionDeadline=None) for n in range(1, 11)])
    health = check_health(GOOD, new)
    assert not health.ok
    assert health.problems == ["the submission deadline is missing or unreadable for 10 of 10 tenders"]


def test_field_the_portal_never_shows_is_not_punished() -> None:
    without_buyer = read([tender(n, buyerName=None) for n in range(1, 11)])
    assert check_health(None, without_buyer).ok
    assert check_health(without_buyer, without_buyer).ok


def test_titles_are_always_required() -> None:
    assert not check_health(None, read([tender(n, title=None) for n in range(1, 6)])).ok


def test_drop_of_more_than_half_is_a_problem() -> None:
    health = check_health(GOOD, read([tender(n) for n in range(1, 5)]))
    assert health.problems == ["the tender count dropped from 10 to 4"]
    assert check_health(GOOD, read([tender(n) for n in range(1, 6)])).ok  # exactly half is fine


def test_tenders_that_closed_on_time_may_disappear() -> None:
    prev = read([tender(n) for n in range(1, 3)] + [tender(n, submissionDeadline=PAST) for n in range(3, 11)])
    assert check_health(prev, read([tender(1), tender(2)])).ok


def test_small_portals_are_not_judged_on_drops() -> None:
    prev = read([tender(n) for n in range(1, 4)])
    assert check_health(prev, read([tender(1)])).ok


def test_changed_id_on_the_same_page_is_a_problem() -> None:
    new = read([tender(n, id=f"NEW-{n}") if n <= 3 else tender(n) for n in range(1, 11)])
    health = check_health(GOOD, new)
    assert health.problems == ["3 tenders now show a different id on the same page than before"]


def test_shared_list_url_does_not_count_as_changed_ids() -> None:
    prev = read([tender(n, url="https://p.example/list") for n in range(1, 6)])
    new = read([tender(n + 100, url="https://p.example/list") for n in range(1, 6)])
    assert not any("different id" in p for p in check_health(prev, new).problems)


def test_document_failures() -> None:
    ok = [tender(n) for n in range(1, 11)]
    assert check_health(GOOD, read(ok, documents_tried=10, documents_failed=5)).ok
    assert not check_health(GOOD, read(ok, documents_tried=10, documents_failed=6)).ok
    assert not check_health(GOOD, read(ok, documents_tried=2, documents_failed=2)).ok
    assert check_health(GOOD, read(ok, documents_tried=1, documents_failed=1)).ok  # one hiccup is not a pattern


def test_document_links_compared_for_continuing_tenders_with_unchanged_status() -> None:
    doc = Document(fileName="notice.pdf", sizeBytes=10, sha256="abc", path="/files/abc")
    before = read(
        [tender(0, status="closed", documents=[doc])] + [tender(n, documents=[doc]) for n in range(1, 5)]
    )
    after = [tender(0, status="closed"), tender(1), tender(2), tender(3, status="withdrawn"), tender(5)]
    health = check_health(before, read(after))
    assert not health.ok
    assert "document links disappeared for 3 of 3" in health.problems[0]
    assert health.missing_document_ids == {"T-0", "T-1", "T-2"}

    # Links can still be present when files were skipped for size. One removal is routine.
    health = check_health(before, read(after, tender_ids_with_document_links={"T-0", "T-1"}))
    assert health.ok and not health.missing_document_ids


def test_small_boamp_list_accepts_new_notices_with_no_deadline():
    import json
    from pathlib import Path

    from app.models import Locale, RawTender
    from brain.normalize import normalize

    path = Path(__file__).parents[1] / "testset/fixtures/boamp-fr/list.json"
    records = json.loads(path.read_text())["results"]
    tenders = [
        normalize(RawTender(url=r["url_avis"], fields={
            "id": r["idweb"], "title": r["objet"], "buyerName": r["nomacheteur"],
            "submissionDeadline": r["datelimitereponse"], "publicationDate": r["dateparution"],
        }), Locale(country="FR")) for r in records
    ]
    before = read([t for t in tenders if t.submissionDeadline])
    assert len(before.tenders) == 1 and len(tenders) == 3
    assert check_health(before, read(tenders)).ok
    # The same portal losing the one deadline it previously displayed still matters.
    after = read([t.model_copy(update={"submissionDeadline": None}) for t in tenders])
    assert not check_health(before, after).ok
