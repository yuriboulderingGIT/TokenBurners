"""Shared data models. OWNER: A1. Changes must be announced to the whole team first.

Field names of API models are camelCase on purpose: they match the API contract
(plan section 3) exactly, so no aliasing is needed.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PortalState = Literal["integrating", "serving", "healing", "failed"]
RunKind = Literal["read", "repair"]
RunResult = Literal["ok", "failed"]
TenderStatus = Literal["open", "closed", "withdrawn"]
ProcedureType = Literal["open", "restricted", "negotiated", "other"]
ContractNature = Literal["works", "services", "supplies"]


# ---------- API shapes (section 3) ----------


class CreatePortal(BaseModel):
    url: str


class PortalOut(BaseModel):
    id: str
    url: str
    state: PortalState
    reason: str | None
    tenderCount: int
    lastSuccessAt: str | None


class RunOut(BaseModel):
    id: str
    kind: RunKind
    startedAt: str
    finishedAt: str | None
    result: RunResult | None


class Document(BaseModel):
    fileName: str
    sizeBytes: int
    sha256: str
    path: str  # our API path, e.g. /files/ab12


class PlaceOfPerformance(BaseModel):
    postalCode: str | None = None
    city: str | None = None
    countryCode: str | None = None  # two letters, e.g. DE


# Optional tender fields are LEFT OUT of the JSON when unknown (golden rule).
OPTIONAL_TENDER_FIELDS = (
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)


class Tender(BaseModel):
    # Required: always present in the JSON, null when the portal doesn't show it.
    id: str  # the portal's own identifier, stable across reads
    url: str
    title: str | None
    buyerName: str | None
    publicationDate: str | None  # YYYY-MM-DD
    submissionDeadline: str | None  # ISO 8601 with offset
    status: TenderStatus
    documents: list[Document] = Field(default_factory=list)
    lastChangedAt: str | None = None  # set by storage, not by the parser
    # Optional ("if shown")
    referenceNumber: str | None = None
    description: str | None = None
    procedureType: ProcedureType | None = None
    contractNature: ContractNature | None = None
    cpvCodes: list[str] | None = None  # 8 digits, no check digit
    placeOfPerformance: PlaceOfPerformance | None = None

    def api_dict(self) -> dict[str, Any]:
        """JSON for the API: required fields always present, unknown optional fields left out."""
        data = self.model_dump(mode="json")
        for key in OPTIONAL_TENDER_FIELDS:
            if data.get(key) is None:
                data.pop(key, None)
        place = data.get("placeOfPerformance")
        if isinstance(place, dict):
            data["placeOfPerformance"] = {k: v for k, v in place.items() if v is not None}
        return data


# ---------- Recipe (section 4.1) ----------


class ListSpec(BaseModel):
    url: str
    item: str  # XPath (html) or JSON path (json_api) selecting one list entry
    next_page: str | None = None
    max_pages: int = 20


class DetailSpec(BaseModel):
    link: str | None = None  # relative to a list item; None = all fields are on the list


class DocumentsSpec(BaseModel):
    link: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)  # multi-step downloads (stretch)


class Locale(BaseModel):
    tz: str | None = None  # e.g. Europe/Berlin
    lang: str | None = None  # e.g. de
    country: str | None = None  # e.g. DE


class Recipe(BaseModel):
    """What the LLM writes and run_recipe() executes. `list` is a Python builtin, hence `list_`."""

    model_config = ConfigDict(populate_by_name=True)

    version: int = 1
    kind: Literal["html", "json_api"] = "html"
    list_: ListSpec = Field(alias="list")
    detail: DetailSpec = Field(default_factory=DetailSpec)
    fields: dict[str, str] = Field(default_factory=dict)
    documents: DocumentsSpec = Field(default_factory=DocumentsSpec)
    locale: Locale = Field(default_factory=Locale)
    learned_by: str | None = None
    learned_at: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)


# ---------- Internal pipeline shapes (section 4.2) ----------


class RawTender(BaseModel):
    """One tender as the recipe executor found it: raw strings, not cleaned yet."""

    url: str  # detail page URL (absolute)
    fields: dict[str, str | list[str] | None] = Field(default_factory=dict)
    document_links: list[str] = Field(default_factory=list)  # absolute URLs


class ReadResult(BaseModel):
    tenders: list[Tender]
    raw_count: int = 0  # how many list entries the recipe found
    documents_tried: int = 0
    documents_failed: int = 0


class Health(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
