"""Field cleanup: raw strings -> contract values. OWNER: B2.

Golden rule: a wrong value is worse than a missing one. When unsure, return None.

STUB: only accepts values that are already ISO formatted. Replace with dateparser-based
multilingual parsing, local time -> offset via the portal's time zone, procedureType /
contractNature mapping, status detection, country codes.
"""

import re
from datetime import date, datetime, timezone

from app.models import Locale, RawTender, Tender

_CPV = re.compile(r"\b(\d{8})(?:-\d)?\b")


def normalize(raw: RawTender, locale: Locale) -> Tender | None:
    """Returns None when the tender has no usable id (it can't be delivered then)."""
    f = raw.fields
    tender_id = _text(f.get("id"))
    if not tender_id:
        return None
    deadline = _iso_datetime(_text(f.get("submissionDeadline")))
    status = "open"
    if deadline and datetime.fromisoformat(deadline) < datetime.now(timezone.utc):
        status = "closed"
    return Tender(
        id=tender_id,
        url=raw.url,
        title=_text(f.get("title")),
        buyerName=_text(f.get("buyerName")),
        publicationDate=_iso_date(_text(f.get("publicationDate"))),
        submissionDeadline=deadline,
        status=status,
        referenceNumber=_text(f.get("referenceNumber")),
        description=_text(f.get("description")),
        cpvCodes=_cpv(f.get("cpvCodes")),
    )


def _text(value: str | list[str] | None) -> str | None:
    if isinstance(value, list):
        value = " ".join(v for v in value if v)
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _iso_date(value: str | None) -> str | None:
    # TODO(B2, B5): "20.11.2026", "20 novembre 2026", ...
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _iso_datetime(value: str | None) -> str | None:
    # TODO(B2, B5): local times without offset -> use locale.tz (zoneinfo).
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # no offset known: unsure -> null
    return parsed.isoformat()


def _cpv(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None
    text = " ".join(value) if isinstance(value, list) else value
    codes = list(dict.fromkeys(_CPV.findall(text)))
    return codes or None
