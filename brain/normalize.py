"""Convert extracted values into the tender API's data types."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dateparser

from app.models import Locale, PlaceOfPerformance, RawTender, Tender

_CPV = re.compile(r"\b([0-9]{8})(?:-[0-9])?\b")
_FULL_YEAR = re.compile(r"(?<!\d)[0-9]{4}(?!\d)")
_NUMERIC_DATE = re.compile(r"^([0-9]{1,2})[./-]([0-9]{1,2})[./-][0-9]{4}(?!\d)")
_CLOCK = re.compile(r"(?<=T)\d{2}|\b\d{1,2}[:h]\d{2}\b|\b\d{1,2}\s*(?:[ap]\.?m\.?|Uhr)\b", re.I)
# ePPS emits English server timestamps even on Greek-language pages.
_SERVER_TIMESTAMP = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"\d{1,2} \d{2}:\d{2}:\d{2} (?:UTC|GMT|CET|CEST|EET|EEST|WET|WEST) \d{4}$"
)
_DATE_ORDERS_BY_COUNTRY = {
    "AT": "DMY", "BE": "DMY", "DE": "DMY", "ES": "DMY", "FR": "DMY", "GB": "DMY",
    "IE": "DMY", "IT": "DMY", "LU": "DMY", "NL": "DMY", "PL": "DMY", "US": "MDY",
}
# Civil time used by mainland European procurement portals.
_COUNTRY_ZONES = {
    "AT": "Europe/Vienna", "BE": "Europe/Brussels", "BG": "Europe/Sofia",
    "CH": "Europe/Zurich", "CY": "Asia/Nicosia", "CZ": "Europe/Prague",
    "DE": "Europe/Berlin", "DK": "Europe/Copenhagen", "EE": "Europe/Tallinn",
    "ES": "Europe/Madrid", "FI": "Europe/Helsinki", "FR": "Europe/Paris",
    "GB": "Europe/London", "GR": "Europe/Athens", "HR": "Europe/Zagreb",
    "HU": "Europe/Budapest", "IE": "Europe/Dublin", "IS": "Atlantic/Reykjavik",
    "IT": "Europe/Rome", "LT": "Europe/Vilnius", "LU": "Europe/Luxembourg",
    "LV": "Europe/Riga", "MT": "Europe/Malta", "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo", "PL": "Europe/Warsaw", "PT": "Europe/Lisbon",
    "RO": "Europe/Bucharest", "SE": "Europe/Stockholm", "SI": "Europe/Ljubljana",
    "SK": "Europe/Bratislava", "UK": "Europe/London",
}
_DAY_FIRST_LANGUAGES = {"de", "es", "fr", "it", "nl", "pl"}
_PROCEDURES = {
    "open": "open", "open procedure": "open", "offenes verfahren": "open",
    "appel d’offres ouvert": "open", "appel d'offres ouvert": "open",
    "openbare procedure": "open", "procedimiento abierto": "open",
    "restricted": "restricted", "restricted procedure": "restricted",
    "nicht offenes verfahren": "restricted", "niet-openbare procedure": "restricted",
    "procedimiento restringido": "restricted",
    "negotiated": "negotiated", "negotiated procedure": "negotiated",
    "verhandlungsverfahren": "negotiated", "procedimiento negociado": "negotiated",
    "other": "other",
}
_NATURES = {
    "works": "works", "bauleistungen": "works", "travaux": "works",
    "werken": "works", "obras": "works",
    "services": "services", "dienstleistungen": "services", "diensten": "services",
    "servicios": "services",
    "supplies": "supplies", "lieferleistungen": "supplies", "fournitures": "supplies",
    "leveringen": "supplies", "suministros": "supplies",
}
_WITHDRAWN = {
    "withdrawn", "cancelled", "canceled", "annulé", "annulée",
    "zurückgezogen", "aufgehoben", "ingetrokken", "cancelado", "anulado",
}
_CLOSED = {"closed", "expired", "clôturé", "geschlossen", "gesloten", "cerrado"}


def normalize(raw: RawTender, locale: Locale) -> Tender | None:
    """Return None when the tender has no unambiguous portal identifier."""
    fields = raw.fields
    tender_id = _single_text(fields.get("id"))
    if not tender_id:
        return None
    if not locale.country:
        try:
            host = urlsplit(raw.url).hostname or ""
        except ValueError:
            host = ""
        _, separator, country = host.rstrip(".").rpartition(".")
        suffix = country.upper() if separator else ""
        if suffix in _COUNTRY_ZONES:
            updates = {"tz": locale.tz or _COUNTRY_ZONES[suffix]}
            # Keep an explicit language-region date order (e.g. en-US on a .de portal).
            if "-" not in (locale.lang or "").replace("_", "-"):
                updates["country"] = "GB" if suffix == "UK" else suffix
            locale = locale.model_copy(update=updates)
    publication = _parse_date(_single_text(fields.get("publicationDate")), locale)
    deadline = _deadline(_single_text(fields.get("submissionDeadline")), locale)
    source_status = _label(fields.get("status"))
    status = "open"
    if source_status in _WITHDRAWN:
        status = "withdrawn"
    elif source_status in _CLOSED or (deadline and deadline <= datetime.now(timezone.utc)):
        status = "closed"
    return Tender(
        id=tender_id,
        url=raw.url,
        title=_single_text(fields.get("title")),
        buyerName=_single_text(fields.get("buyerName")),
        publicationDate=publication.date().isoformat() if publication else None,
        submissionDeadline=deadline.isoformat() if deadline else None,
        status=status,
        referenceNumber=_single_text(fields.get("referenceNumber")),
        description=_text(fields.get("description")),
        procedureType=_PROCEDURES.get(_label(fields.get("procedureType"))),
        contractNature=_NATURES.get(_label(fields.get("contractNature"))),
        cpvCodes=_cpv(fields.get("cpvCodes")),
        placeOfPerformance=_performance_place(fields),
    )


def _text(value: str | list[str] | None) -> str | None:
    if isinstance(value, list):
        value = " ".join(v for v in value if v)
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _single_text(value: str | list[str] | None) -> str | None:
    if isinstance(value, list):
        distinct = {_text(item) for item in value} - {None}
        return distinct.pop() if len(distinct) == 1 else None
    return _text(value)


def _label(value: str | list[str] | None) -> str:
    return (_single_text(value) or "").casefold()


def _parse_date(value: str | None, locale: Locale) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    if len(value) > 150 or not _FULL_YEAR.search(value):
        return None

    lang, _, country_from_lang = (locale.lang or "").replace("_", "-").partition("-")
    lang = "en" if _SERVER_TIMESTAMP.fullmatch(value) else lang.lower()
    country = (locale.country or country_from_lang).upper()
    settings = {"STRICT_PARSING": True, "PARSERS": ["absolute-time"]}
    numeric = _NUMERIC_DATE.match(value)
    if numeric:
        first, second = map(int, numeric.groups())
        order = _DATE_ORDERS_BY_COUNTRY.get(country)
        if order is None and lang in _DAY_FIRST_LANGUAGES:
            order = "DMY"
        if order is None and max(first, second) <= 12 and first != second:
            return None
        if order:
            settings["DATE_ORDER"] = order
            settings["PREFER_LOCALE_DATE_ORDER"] = False

    value = re.sub(r"\b(\d{1,2})h(\d{2})\b", r"\1:\2", value, flags=re.I)
    try:
        return dateparser.parse(value, languages=[lang] if lang else None, settings=settings)
    except (ValueError, OverflowError):
        return None


def _deadline(value: str | None, locale: Locale) -> datetime | None:
    if value:
        # Charité and similar German listings write local times as "10:00h".
        value = re.sub(r"(\b\d{1,2}:\d{2})h\b", r"\1", value, flags=re.I)
    if not value or not _CLOCK.search(value):
        return None
    parsed = _parse_date(value, locale)
    if parsed is None or parsed.tzinfo is not None:
        return parsed
    zone = locale.tz or _COUNTRY_ZONES.get((locale.country or "").upper())
    if not zone:
        return None
    try:
        local_tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    localized = parsed.replace(tzinfo=local_tz)
    if localized.utcoffset() != localized.replace(fold=1).utcoffset():
        return None  # A DST transition leaves the wall time missing or ambiguous.
    return localized


def _performance_place(fields: dict[str, str | list[str] | None]) -> PlaceOfPerformance | None:
    postal_code = _single_text(fields.get("placeOfPerformance.postalCode"))
    city = _single_text(fields.get("placeOfPerformance.city"))
    country = (_single_text(fields.get("placeOfPerformance.countryCode")) or "").upper()
    country_code = country if re.fullmatch(r"[A-Z]{2}", country) else None
    if not (postal_code or city or country_code):
        return None
    return PlaceOfPerformance(postalCode=postal_code, city=city, countryCode=country_code)


def _cpv(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None
    text = " ".join(value) if isinstance(value, list) else value
    codes = list(dict.fromkeys(_CPV.findall(text)))
    return codes or None
