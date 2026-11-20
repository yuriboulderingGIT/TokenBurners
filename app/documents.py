"""Document pipeline. OWNER: A2.

For every document link of a tender:
- Already downloaded from this link for this tender? Reuse it: no request, so refreshes stay cheap.
- Otherwise stream it through the Fetcher (1 req/s per portal), never holding it in memory.
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
import logging
import os
import re
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from app.config import FILES_DIR, MAX_DOC_BYTES
from app.db import DB, new_id
from app.fetcher import PortalFetcher, TooLarge
from app.models import Document, Tender
from brain.blocks import detect_block

log = logging.getLogger("watchtower.documents")

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024  # per downloaded archive, all members together (zip bomb guard)
MAX_ZIP_MEMBERS = 1000
MAX_ZIP_DEPTH = 3  # archive inside archive inside archive
_CHUNK = 1024 * 1024

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


@dataclass
class StoredDocuments:
    """What happened to one tender's document links."""

    documents: list[Document] = field(default_factory=list)
    failed: int = 0  # links that could not be downloaded (network errors, HTTP errors, web pages)
    blocked: int = 0  # links that led to a login or captcha page
    block_kind: str | None = None  # "login" | "captcha" for the first blocked link
    too_large: int = 0  # files skipped because they are over 250 MB (allowed by the brief)


async def store_documents(
    db: DB, portal_id: str, tender: Tender, links: list[str], fetch: PortalFetcher
) -> StoredDocuments:
    result = StoredDocuments()
    portal_dir = FILES_DIR / portal_id
    incoming = portal_dir / ".incoming"
    _remove_stale(incoming)
    for url in dict.fromkeys(links):
        known = db.documents_from_source(portal_id, tender.id, url)
        if known and all(Path(row["path_on_disk"]).exists() for row in known):
            result.documents.extend(_document(row) for row in known)
            continue

        try:
            downloaded = await fetch.stream(url, MAX_DOC_BYTES, incoming)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                _blocked(result, "login")
            else:
                result.failed += 1
            log.info("document %s: HTTP %s", url, e.response.status_code)
            continue
        except (httpx.HTTPError, httpx.InvalidURL, OSError) as e:
            result.failed += 1
            log.info("document %s: %s", url, type(e).__name__)
            continue
        # PortalCancelled is deliberately not caught: the worker must stop for a deleted portal.

        if isinstance(downloaded, TooLarge):
            result.too_large += 1
            log.info("document %s skipped: over %d bytes", url, MAX_DOC_BYTES)
            continue

        page_block = _web_page(downloaded.path)
        if page_block is not None:
            downloaded.path.unlink(missing_ok=True)
            if page_block:
                _blocked(result, page_block)
            else:
                result.failed += 1  # an ordinary web page is not a document
            continue

        name = _file_name(downloaded.file_name, url)
        try:
            entries, skipped = await asyncio.to_thread(
                _finalize, downloaded.path, name, downloaded.size_bytes, downloaded.sha256, portal_dir
            )
        except OSError as e:
            downloaded.path.unlink(missing_ok=True)
            result.failed += 1
            log.warning("document %s could not be stored: %s", url, e)
            continue
        result.too_large += skipped
        if known:  # files were missing on disk: replace the old rows' files with fresh ones
            for row in known:
                db.execute("DELETE FROM documents WHERE file_id = ?", (row["file_id"],))
        for entry in entries:
            db.document_add(
                portal_id, tender.id, entry.name, entry.size, entry.sha256, str(entry.path),
                source_url=url, file_id=entry.file_id,
            )
            result.documents.append(
                Document(fileName=entry.name, sizeBytes=entry.size, sha256=entry.sha256, path=f"/files/{entry.file_id}")
            )
    return result


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


def _web_page(path: Path) -> str | None:
    """None: not a web page. "": an ordinary web page. "login"/"captcha": a gate."""
    with open(path, "rb") as f:
        head = f.read(256 * 1024)
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if not (start.startswith(b"<!doctype html") or start.startswith(b"<html") or b"<html" in start[:1024]):
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


def _finalize(path: Path, name: str, size: int, sha256: str, portal_dir: Path) -> tuple[list[_Entry], int]:
    """Move a download into place, or unpack it when it is an archive. Returns (files, skipped)."""
    portal_dir.mkdir(parents=True, exist_ok=True)
    if _is_archive(path, name):
        budget = _Budget()
        members = _unpack(path, portal_dir, 1, budget)
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
    if suffix in _NOT_ARCHIVES or not zipfile.is_zipfile(path):
        return False
    if suffix == ".zip":
        return True
    try:
        with zipfile.ZipFile(path) as archive:
            return not (_CONTAINER_MARKERS & set(archive.namelist()))
    except (zipfile.BadZipFile, OSError, ValueError):
        return False


def _unpack(archive_path: Path, out_dir: Path, depth: int, budget: _Budget) -> list[_Entry]:
    entries: list[_Entry] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            for info in members[:MAX_ZIP_MEMBERS]:
                name = _member_name(info.filename)
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
                    inner = _unpack(target, out_dir, depth + 1, budget)
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
