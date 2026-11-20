"""The ONLY way to make HTTP calls. OWNER: A1.

- At most 1 request per second per portal, shared by pages, documents AND the browser
  (Playwright code must call `wait_turn()` before letting a request through).
- Timeouts and retries with backoff.
- DELETE /portals/{id} calls `cancel()`: the next request for that portal raises
  PortalCancelled, so the system stops calling it within one minute.

Brain code receives a PortalFetcher (already bound to one portal), so it never has to
pass portal ids around:  `await fetch.get(url)`.

Notes for other agents:
- Never use httpx/requests/urllib directly anywhere else: that breaks the 1 req/s rule.
- Redirects are followed one rate-limited hop at a time. Pass follow_redirects=False
  when the caller needs to check a destination before requesting it.
- get() retries 429/5xx itself and returns the final response; check status_code yourself.
- Don't catch PortalCancelled (or bare Exception around fetch calls without re-raising it):
  the worker needs it to stop work on a deleted portal.
- Documents: use stream(url, max_bytes, dest_dir); it never holds a file in memory and
  returns TooLarge instead of raising when the cap is passed.
"""

import asyncio
import hashlib
import logging
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import HTTP_TIMEOUT, MIN_REQUEST_INTERVAL, USER_AGENT

log = logging.getLogger("watchtower.fetcher")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 10


class PortalCancelled(Exception):
    """The portal was deleted; stop working on it."""


@dataclass
class TooLarge:
    url: str
    size_bytes: int | None  # None when the server didn't say and we stopped while streaming


@dataclass
class Downloaded:
    path: Path
    size_bytes: int
    sha256: str
    file_name: str | None  # from Content-Disposition, if given


class Fetcher:
    def __init__(
        self,
        min_interval: float = MIN_REQUEST_INTERVAL,
        timeout: float = HTTP_TIMEOUT,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.retries = retries
        self._client = httpx.AsyncClient(
            follow_redirects=False,  # _send follows them, so every hop waits its turn
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._cancelled: set[str] = set()
        self.request_log: deque[tuple[str, float, str]] = deque(maxlen=2000)  # (portal, time, url)

    def for_portal(self, portal_id: str) -> "PortalFetcher":
        return PortalFetcher(self, portal_id)

    def cancel(self, portal_id: str) -> None:
        self._cancelled.add(portal_id)

    def uncancel(self, portal_id: str) -> None:
        self._cancelled.discard(portal_id)

    async def wait_turn(self, portal_id: str, url: str = "") -> None:
        """Block until this portal may make its next request. Every request must call this."""
        if portal_id in self._cancelled:
            raise PortalCancelled(portal_id)
        lock = self._locks.setdefault(portal_id, asyncio.Lock())
        async with lock:
            # Loop: timers can wake a few ms early (notably on Windows).
            while (wait := self._last.get(portal_id, 0.0) + self.min_interval - time.monotonic()) > 0:
                await asyncio.sleep(wait + 0.005)
            if portal_id in self._cancelled:
                raise PortalCancelled(portal_id)
            self._last[portal_id] = time.monotonic()
            self.request_log.append((portal_id, self._last[portal_id], url))

    async def get(
        self, portal_id: str, url: str, *, follow_redirects: bool = True, **kw
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = self._client.build_request("GET", url, **kw)
                response = await self._send(portal_id, request, follow_redirects=follow_redirects)
                if response.status_code not in RETRY_STATUSES or attempt == self.retries:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
            except httpx.TransportError as e:
                last_error = e
                if attempt == self.retries:
                    raise
            await asyncio.sleep(2 ** (attempt + 1))  # backoff: 2s, 4s
        raise last_error or RuntimeError("unreachable")

    async def stream(
        self, portal_id: str, url: str, max_bytes: int, dest_dir: Path | None = None
    ) -> Downloaded | TooLarge:
        """Download to a file without holding it in memory. Stops past max_bytes."""
        dest_dir = dest_dir or Path(tempfile.gettempdir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        response = await self._send(portal_id, self._client.build_request("GET", url), stream=True)
        try:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_bytes:
                return TooLarge(url, int(length))
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False) as out:
                path = Path(out.name)
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if portal_id in self._cancelled:  # DELETE must stop a long download within 1 min
                        out.close()
                        path.unlink(missing_ok=True)
                        raise PortalCancelled(portal_id)
                    if size > max_bytes:
                        out.close()
                        path.unlink(missing_ok=True)
                        return TooLarge(url, None)
                    digest.update(chunk)
                    out.write(chunk)
            return Downloaded(path, size, digest.hexdigest(), _filename(response))
        finally:
            await response.aclose()

    async def _send(
        self, portal_id: str, request: httpx.Request, *,
        stream: bool = False, follow_redirects: bool = True,
    ) -> httpx.Response:
        """Send one request, following redirects by hand: each hop is a request and waits its turn."""
        for _ in range(MAX_REDIRECTS + 1):
            await self.wait_turn(portal_id, str(request.url))
            response = await self._client.send(request, stream=stream)
            if not follow_redirects or response.next_request is None:
                return response
            await response.aclose()
            request = response.next_request
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=request)

    async def aclose(self) -> None:
        await self._client.aclose()


class PortalFetcher:
    """A Fetcher bound to one portal. This is what brain/ functions receive."""

    def __init__(self, fetcher: Fetcher, portal_id: str) -> None:
        self.fetcher = fetcher
        self.portal_id = portal_id

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self.fetcher.get(self.portal_id, url, **kw)

    async def stream(self, url: str, max_bytes: int, dest_dir: Path | None = None) -> Downloaded | TooLarge:
        return await self.fetcher.stream(self.portal_id, url, max_bytes, dest_dir)

    async def wait_turn(self, url: str = "") -> None:
        """For Playwright: call this before letting the browser make a request."""
        await self.fetcher.wait_turn(self.portal_id, url)


class FakeFetcher(Fetcher):
    """Serves saved pages instead of the internet. For tests and offline development.

    pages: {absolute_url: body (str or bytes)}. Unknown URLs answer 404.
    """

    def __init__(self, pages: dict[str, str | bytes], min_interval: float = 0.0) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = pages.get(str(request.url))
            if body is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode())

        super().__init__(min_interval=min_interval, retries=0, transport=httpx.MockTransport(handler))


def _filename(response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip('"') or None
    return None
