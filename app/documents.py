"""Document pipeline. OWNER: A2.

For every document link of a tender:
- Revalidate known files with ETag/Last-Modified. A periodic full download also catches stale validators.
- Stream downloads through the Fetcher; a changed file receives a new immutable /files URL.
- Over 250 MB (ZIPs included) -> skipped; the brief allows leaving those out.
- A ZIP archive is unpacked (also nested ZIPs) and its files are delivered instead of the archive.
  Word/Excel/OpenDocument files are ZIP containers too, but are documents: they stay whole.
- A link that returns a web page instead of a file is not a document. If that page is a login
  or captcha, the link counts as blocked; the worker reports the portal as blocked when every
  document link is.
- Each stored file gets a row in the documents table and is served at /files/{fileId}.

Unpacking runs in a thread so a large archive doesn't freeze the API.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import zipfile
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from app.config import FILES_DIR, MAX_DOC_BYTES
from app.db import DB, new_id
from app.fetcher import Downloaded, NotModified, PortalFetcher, TooLarge
from app.models import Document, Tender
from brain.blocks import Blocked, detect_block
from brain.run_recipe import RecipeReadError

log = logging.getLogger("watchtower.documents")

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024  # per downloaded archive, all members together (zip bomb guard)
MAX_ZIP_MEMBERS = 1000
MAX_ZIP_DEPTH = 3  # archive inside archive inside archive
_CHUNK = 1024 * 1024
UNVALIDATED_REFRESH_SECONDS = 15 * 60
FULL_REFRESH_SECONDS = 24 * 60 * 60

# Files that may be ZIP containers internally but are documents in their own right.
_NOT_ARCHIVES = {
    ".pdf", ".doc", ".docx", ".docm", ".dot", ".dotx", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".odg", ".rtf", ".txt", ".csv", ".xml", ".json", ".gaeb", ".x83", ".x84",
    ".d83", ".p83", ".dwg", ".dxf", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".epub", ".jar",
    ".7z", ".rar", ".gz", ".tar",
}
# Markers of ZIP-based document formats (OOXML, OpenDocument, Java/EPUB).
_CONTAINER_MARKERS = {"[Content_Types].xml", "mimetype", "META-INF/manifest.xml", "META-INF/MANIFEST.MF"}
_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"|?*]')
_SPLIT_ZIP = re.compile(r"\.zip\.\d{3,}$", re.I)


@dataclass
class StoredDocuments:
    """What happened to one tender's document links."""

    documents: list[Document] = field(default_factory=list)
    failed: int = 0  # links that could not be downloaded (network errors, HTTP errors, web pages)
    blocked: int = 0  # links that led to a login or captcha page
    block_kind: str | None = None  # "login" | "captcha" for the first blocked link
    too_large: int = 0  # files skipped because they are over 250 MB (allowed by the brief)
    failed_revalidations: int = 0
    cached_files: int = 0
    access_probes: int = 0


async def store_documents(
    db: DB, portal_id: str, tender: Tender, links: list[str], fetch: PortalFetcher,
    revalidate: set[str] | None = None, *, expected_names: dict[str, str] | None = None,
) -> StoredDocuments:
    """`revalidate` limits which known sources this read may check again (None: all that are due)."""
    result = StoredDocuments()
    portal_dir = FILES_DIR / portal_id
    incoming = portal_dir / ".incoming"
    _remove_stale(incoming)
    for url in dict.fromkeys(links):
        key = (portal_id, tender.id, hashlib.sha256(url.encode()).hexdigest())
        source = db.one("SELECT * FROM document_sources WHERE portal_id=? AND tender_id=? AND source_key=?", key)
        known = db.source_files(source["file_ids"]) if source else []
        complete = bool(known) and len(known) == len(json.loads(source["file_ids"])) and all(
            Path(row["path_on_disk"]).is_file() for row in known
        )
        if complete and _SPLIT_ZIP.search(source["file_name"] or ""):
            # An older reader could mistake the final volume's central directory for a
            # standalone ZIP and publish a subset of its members in place of the volume.
            complete = len(known) == 1 and known[0]["sha256"] == source["sha256"]
        validators = _revalidation_headers(source) if complete else {}
        if complete and revalidate is not None and key[2] not in revalidate:
            validators = None  # outside this read's revalidation budget: serve the verified revision
        if validators is None:
            if key[2] == getattr(fetch, "probe_source", None):
                probe = await fetch.document_prefix(url)
                kind = detect_block(probe.text, probe.status_code)
                if kind:
                    raise Blocked(kind, "the previously public document now requires access")
                head = probe.content.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
                if (not probe.is_success or not head or "html" in probe.headers.get("content-type", "").lower()
                        or b"<html" in head[:1024] or head.startswith((b"<!doctype html", b"<form"))):
                    raise RecipeReadError("a previously public document no longer returns file bytes")
                result.access_probes += 1
                db.execute("UPDATE document_sources SET last_access_check=? WHERE portal_id=? AND tender_id=? AND source_key=?",
                           (time.time(), *key))
            result.cached_files += len(known)
            result.documents.extend(_document(row) for row in known)
            continue

        def keep_verified(problem: str) -> None:
            # A known file stays the last verified revision; one source's outage must not stop the read.
            result.failed_revalidations += 1
            result.cached_files += len(known)
            result.documents.extend(_document(row) for row in known)
            log.info("document %s: revalidation failed (%s), serving the verified revision", url, problem)

        try:
            downloaded = await fetch.stream(url, MAX_DOC_BYTES, incoming, validators=validators,
                                            validator_target=source["validator_target"] if source else None)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                _blocked(result, "login")  # a login now guards the file: the brief's failed state
            elif complete:
                keep_verified(f"HTTP {e.response.status_code}")
            else:
                result.failed += 1
            log.info("document %s: HTTP %s", url, e.response.status_code)
            continue
        except (httpx.HTTPError, httpx.InvalidURL, OSError) as e:
            if complete:
                keep_verified(type(e).__name__)
            else:
                result.failed += 1
            log.info("document %s: %s", url, type(e).__name__)
            continue
        # PortalCancelled is deliberately not caught: the worker must stop for a deleted portal.

        if isinstance(downloaded, NotModified):
            db.execute(
                "UPDATE document_sources SET last_verified_at=?, etag=COALESCE(?,etag), "
                "last_modified=COALESCE(?,last_modified) WHERE portal_id=? AND tender_id=? AND source_key=?",
                (time.time(), downloaded.etag, downloaded.last_modified, *key),
            )
            result.documents.extend(_document(row) for row in known)
            continue
        if isinstance(downloaded, TooLarge):
            result.too_large += 1
            log.info("document %s skipped: over %d bytes", url, MAX_DOC_BYTES)
            continue

        page_block = _web_page(downloaded.path, downloaded.content_type)
        if page_block is not None:
            downloaded.path.unlink(missing_ok=True)
            if page_block:
                _blocked(result, page_block)  # login or captcha, also for a file known before
            elif complete:
                keep_verified("web page instead of a document")
            else:
                result.failed += 1  # an ordinary web page is not a document
            continue

        expected = (expected_names or {}).get(url)
        if expected and not _matches_advertised_file(downloaded.path, expected, downloaded.file_name):
            downloaded.path.unlink(missing_ok=True)
            result.failed += 1
            continue
        name = _file_name(downloaded.file_name or expected, url)
        verified = time.time()
        if complete and source["sha256"] == downloaded.sha256 and source["file_name"] == name:
            downloaded.path.unlink(missing_ok=True)
            db.execute(
                "UPDATE document_sources SET etag=?,last_modified=?,validator_target=?,last_verified_at=?, "
                "last_downloaded_at=? WHERE portal_id=? AND tender_id=? AND source_key=?",
                (downloaded.etag, downloaded.last_modified, downloaded.validator_target, verified, verified, *key),
            )
            result.documents.extend(_document(row) for row in known)
            continue
        try:
            entries, skipped = await asyncio.to_thread(
                _finalize, downloaded.path, name, downloaded.size_bytes, downloaded.sha256, portal_dir,
                (urlsplit(url).hostname or "").rsplit(".", 1)[-1].lower(),
            )
        except OSError as e:
            downloaded.path.unlink(missing_ok=True)
            if complete:
                keep_verified(f"could not store the new revision: {e}")
            else:
                result.failed += 1
            log.warning("document %s could not be stored: %s", url, e)
            continue
        result.too_large += skipped
        result.documents.extend(_save_revision(db, key, url, name, downloaded, entries, verified))
    return result


def _revalidation_headers(source: sqlite3.Row) -> dict[str, str] | None:
    """None while locally fresh, {} for a full read, or the validators for a conditional read."""
    elapsed = time.time() - source["last_downloaded_at"]
    if elapsed < 0 or elapsed >= FULL_REFRESH_SECONDS:
        return {}
    headers = {}
    if source["etag"]:
        headers["If-None-Match"] = source["etag"]
    if source["last_modified"]:
        headers["If-Modified-Since"] = source["last_modified"]
    if headers or elapsed >= UNVALIDATED_REFRESH_SECONDS:
        return headers
    return None


def _save_revision(
    db: DB, key: tuple[str, str, str], url: str, name: str, downloaded: Downloaded,
    entries: list["_Entry"], verified: float,
) -> list[Document]:
    """Preserve immutable URLs for identical ZIP members, including restored and legacy files."""
    history = defaultdict(deque)
    portal_id, tender_id, _ = key
    for row in db.documents_from_source(portal_id, tender_id, url):
        history[(row["file_name"], row["sha256"])].append(row)
    file_ids = []
    try:
        with db.transaction() as conn:
            for entry in entries:
                matches = history[(entry.name, entry.sha256)]
                if matches:
                    row = matches.popleft()
                    if Path(row["path_on_disk"]).is_file():
                        entry.path.unlink()
                    else:
                        os.replace(entry.path, row["path_on_disk"])
                    file_ids.append(row["file_id"])
                else:
                    db.document_add(portal_id, tender_id, entry.name, entry.size, entry.sha256,
                                    str(entry.path), source_url=url, file_id=entry.file_id)
                    file_ids.append(entry.file_id)
            conn.execute(
                "INSERT OR REPLACE INTO document_sources (portal_id,tender_id,source_key,sha256,file_name,"
                "file_ids,etag,last_modified,validator_target,last_verified_at,last_downloaded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (*key, downloaded.sha256, name, json.dumps(file_ids), downloaded.etag, downloaded.last_modified,
                 downloaded.validator_target, verified, verified),
            )
    except BaseException:
        for entry in entries:
            entry.path.unlink(missing_ok=True)
        raise
    return [_document(row) for row in db.source_files(json.dumps(file_ids))]


def _document(row) -> Document:
    return Document(
        fileName=row["file_name"], sizeBytes=row["size_bytes"], sha256=row["sha256"], path=f"/files/{row['file_id']}"
    )


def _blocked(result: StoredDocuments, kind: str) -> None:
    result.blocked += 1
    result.block_kind = result.block_kind or kind


def _remove_stale(incoming: Path, max_age_seconds: float = 3600) -> None:
    """Partial downloads left by a dropped connection. Reads of one portal never overlap."""
    if not incoming.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    for leftover in incoming.iterdir():
        try:
            if leftover.is_file() and leftover.stat().st_mtime < cutoff:
                leftover.unlink()
        except OSError:
            pass


# ---------- what did we download? ----------


def _matches_advertised_file(path: Path, label: str, returned: str | None) -> bool:
    """A clicked PDF button must actually yield a PDF, including for extensionless URLs."""
    suffix = PurePosixPath(label).suffix.lower()
    if returned and PurePosixPath(returned).suffix.lower() != suffix:
        return False
    with path.open("rb") as stream:
        head = stream.read(1024)
    if suffix == ".pdf":
        return b"%PDF-" in head
    if suffix in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".zip"}:
        return zipfile.is_zipfile(path)
    return bool(head) and not head.lstrip().lower().startswith((b"<html", b"<!doctype html", b"{", b"["))


def _web_page(path: Path, content_type: str | None = None) -> str | None:
    """None: not a web page. "": an ordinary web page. "login"/"captcha": a gate."""
    with open(path, "rb") as f:
        head = f.read(256 * 1024)
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    html_type = bool(content_type and "text/html" in content_type.casefold())
    if not html_type and not (start.startswith(b"<!doctype html") or start.startswith(b"<html") or b"<html" in start[:1024]):
        return None
    return detect_block(head.decode("utf-8", errors="replace"), 200) or ""


def _file_name(header_name: str | None, url: str) -> str:
    """Name from Content-Disposition, else from the URL path, else from a file-like query value."""
    candidates = [header_name or ""]
    parts = urlsplit(url)
    candidates.append(unquote(PurePosixPath(parts.path).name))
    candidates.extend(unquote(v) for _, v in parse_qsl(parts.query) if re.search(r"\.\w{2,5}$", v))
    names = [_clean_name(c) for c in candidates]
    with_extension = [n for n in names if n and PurePosixPath(n).suffix]
    return (with_extension or [n for n in names if n] or ["document"])[0]


def _clean_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME_CHARS.sub("_", name).strip(" .")
    return name[:200]


# ---------- storing and unpacking (runs in a thread) ----------


@dataclass
class _Entry:
    file_id: str
    name: str
    path: Path
    size: int
    sha256: str


class _Budget:
    def __init__(self) -> None:
        self.remaining = MAX_UNPACKED_BYTES
        self.skipped = 0  # members over 250 MB, or past the archive budget


class _LimitExceeded(Exception):
    pass


def _finalize(path: Path, name: str, size: int, sha256: str, portal_dir: Path, country: str = "") -> tuple[list[_Entry], int]:
    """Move a download into place, or unpack it when it is an archive. Returns (files, skipped)."""
    portal_dir.mkdir(parents=True, exist_ok=True)
    if _is_archive(path, name):
        budget = _Budget()
        members = _unpack(path, portal_dir, 1, budget, country)
        if members:
            path.unlink(missing_ok=True)
            return members, budget.skipped
        # Nothing usable inside (empty, encrypted, damaged): deliver the archive itself.
    file_id = new_id()
    target = portal_dir / file_id
    os.replace(path, target)
    return [_Entry(file_id, name, target, size, sha256)], 0


def _is_archive(path: Path, name: str) -> bool:
    suffix = PurePosixPath(name).suffix.lower()
    if _SPLIT_ZIP.search(name) or suffix in _NOT_ARCHIVES or not zipfile.is_zipfile(path):
        return False
    if suffix == ".zip":
        return True
    try:
        with zipfile.ZipFile(path) as archive:
            return not (_CONTAINER_MARKERS & set(archive.namelist()))
    except (zipfile.BadZipFile, OSError, ValueError):
        return False


def _zip_name(info: zipfile.ZipInfo, country: str) -> str:
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name  # Newer ZIP readers already apply the Unicode Path extra field.
    # Honor the Info-ZIP Unicode Path field only when it names this exact member.
    extra = info.extra
    while len(extra) >= 4:
        size = int.from_bytes(extra[2:4], "little")
        payload = extra[4:4 + size]
        if (extra[:2] == b"up" and len(payload) == size and size > 5 and payload[0] == 1
                and int.from_bytes(payload[1:5], "little") == zlib.crc32(raw)):
            try:
                return payload[5:].decode("utf-8")
            except UnicodeDecodeError:
                pass
        extra = extra[4 + size:]
    # A legacy DOS name can decode into box-drawing or mathematical symbols under CP437.
    if not name.isascii() and country in {"cz", "sk", "pl", "hu"}:
        decoded = raw.decode("cp852")
        if sum(c.isalpha() for c in decoded) > sum(c.isalpha() for c in name):
            return decoded
    return name


def _unpack(archive_path: Path, out_dir: Path, depth: int, budget: _Budget, country: str) -> list[_Entry]:
    entries: list[_Entry] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            for info in members[:MAX_ZIP_MEMBERS]:
                name = _member_name(_zip_name(info, country))
                if name is None:
                    continue
                if info.file_size > MAX_DOC_BYTES or info.file_size > budget.remaining:
                    budget.skipped += 1
                    continue
                file_id = new_id()
                target = out_dir / file_id
                try:
                    size, sha256 = _extract(archive, info, target, min(MAX_DOC_BYTES, budget.remaining))
                except _LimitExceeded:  # the header lied about the size
                    target.unlink(missing_ok=True)
                    budget.skipped += 1
                    continue
                except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError, OSError):
                    target.unlink(missing_ok=True)  # encrypted, unsupported compression or damaged
                    continue
                budget.remaining -= size
                if depth < MAX_ZIP_DEPTH and _is_archive(target, name):
                    inner = _unpack(target, out_dir, depth + 1, budget, country)
                    if inner:
                        target.unlink(missing_ok=True)
                        entries.extend(inner)
                        continue
                entries.append(_Entry(file_id, name, target, size, sha256))
    except (zipfile.BadZipFile, OSError, ValueError):
        for entry in entries:
            entry.path.unlink(missing_ok=True)
        return []
    return entries


def _member_name(raw: str) -> str | None:
    parts = PurePosixPath(raw.replace("\\", "/")).parts
    if not parts or parts[0] == "__MACOSX":
        return None
    base = parts[-1]
    if base in (".DS_Store", "Thumbs.db", "desktop.ini") or base.startswith("._"):
        return None  # operating-system junk, not tender documents
    return _clean_name(base) or None


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(info) as src, open(target, "wb") as dst:
        while chunk := src.read(_CHUNK):
            size += len(chunk)
            if size > limit:
                raise _LimitExceeded()
            digest.update(chunk)
            dst.write(chunk)
    return size, digest.hexdigest()
