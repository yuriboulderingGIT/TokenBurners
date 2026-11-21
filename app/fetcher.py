"""The ONLY way to make HTTP calls. OWNER: A1.

- At most 1 request per second per portal and per destination host, shared by pages, documents AND the browser
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
from http.cookiejar import CookieJar, DefaultCookiePolicy
import logging
import tempfile
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path
from urllib.parse import unquote

import httpx

from app.config import HTTP_TIMEOUT, MIN_REQUEST_INTERVAL, PUBLIC_DESTINATIONS_ONLY, USER_AGENT
from app.destinations import DestinationDenied, PublicHTTPTransport

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
    etag: str | None
    last_modified: str | None
    validator_target: str


@dataclass
class NotModified:
    etag: str | None
    last_modified: str | None


def _validator_target(url: httpx.URL) -> str:
    # Bind validators to their resource without persisting expiring redirect URLs.
    return hashlib.sha256(str(url.copy_with(fragment=None)).encode()).hexdigest()


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
            # PUBLIC_DESTINATIONS_ONLY=1 checks every resolved address. A proxy would resolve the
            # destination outside that check, so the environment's proxy settings are then ignored.
            transport=transport if transport is not None or not PUBLIC_DESTINATIONS_ONLY else PublicHTTPTransport(),
            trust_env=not PUBLIC_DESTINATIONS_ONLY,
            headers={"User-Agent": USER_AGENT},
            # The shared client keeps no cookies; each portal has its own jar (see _send).
            cookies=CookieJar(DefaultCookiePolicy(allowed_domains=[])),
        )
        self._jars: dict[str, httpx.Cookies] = {}
        self._portal_last: dict[str, float] = {}
        self._host_last: dict[str, float] = {}
        self._portal_locks: dict[str, asyncio.Lock] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._cancelled: set[str] = set()
        self.request_log: deque[tuple[str, float, str]] = deque(maxlen=2000)  # (portal, time, url)
        self.on_request: Callable[[str], None] | None = None

    def for_portal(self, portal_id: str) -> "PortalFetcher":
        return PortalFetcher(self, portal_id)

    def cancel(self, portal_id: str) -> None:
        self._cancelled.add(portal_id)

    def uncancel(self, portal_id: str) -> None:
        self._cancelled.discard(portal_id)

    async def wait_turn(self, portal_id: str, url: str = "") -> None:
        """Block until this portal may make its next request. Every request must call this."""
        async with self._request_slot(portal_id, url):
            pass

    @asynccontextmanager
    async def _request_slot(self, portal_id: str, url: str):
        """Space requests from receipt of the previous response, including slow first connections."""
        if portal_id in self._cancelled:
            raise PortalCancelled(portal_id)
        host = httpx.URL(url).raw_host.decode("ascii").rstrip(".")
        portal_lock = self._portal_locks.setdefault(portal_id, asyncio.Lock())
        host_lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with portal_lock, host_lock:
            last = max(self._portal_last.get(portal_id, 0), self._host_last.get(host, 0))
            while (wait := last + self.min_interval - time.monotonic()) > 0:
                await asyncio.sleep(wait + 0.005)
            if portal_id in self._cancelled:
                raise PortalCancelled(portal_id)
            now = time.monotonic()
            self.request_log.append((portal_id, now, url))
            try:
                yield
            finally:
                self._portal_last[portal_id] = self._host_last[host] = time.monotonic()

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
        self, portal_id: str, url: str, max_bytes: int, dest_dir: Path | None = None, *,
        validators: dict[str, str] | None = None, validator_target: str | None = None,
    ) -> Downloaded | TooLarge | NotModified:
        """Download to a file without holding it in memory. Stops past max_bytes."""
        dest_dir = dest_dir or Path(tempfile.gettempdir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        response = await self._send(portal_id, self._client.build_request("GET", url), stream=True,
                                    validators=validators, validator_target=validator_target)
        try:
            etag = response.headers.get("etag")
            last_modified = response.headers.get("last-modified")
            if response.status_code == 304 and validators and _validator_target(response.request.url) == validator_target:
                return NotModified(etag, last_modified)
            response.raise_for_status()
            if response.status_code != 200:
                raise httpx.HTTPStatusError("expected a complete document", request=response.request, response=response)
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
            return Downloaded(path, size, digest.hexdigest(), _filename(response), etag, last_modified,
                              _validator_target(response.request.url))
        finally:
            await response.aclose()

    async def _send(
        self, portal_id: str, request: httpx.Request, *,
        stream: bool = False, follow_redirects: bool = True,
        validators: dict[str, str] | None = None, validator_target: str | None = None,
    ) -> httpx.Response:
        """Send one request, following redirects by hand: each hop is a request and waits its turn."""
        for _ in range(MAX_REDIRECTS + 1):
            if request.url.scheme not in {"http", "https"} or request.url.userinfo:
                raise DestinationDenied("portal requests require an HTTP(S) destination without credentials")
            if validators:
                if _validator_target(request.url) == validator_target:
                    request.headers.update(validators)
                else:
                    for name in validators:
                        request.headers.pop(name, None)
            async with self._request_slot(portal_id, str(request.url)):
                jar = self._jars.setdefault(portal_id, httpx.Cookies())
                jar.set_cookie_header(request)
                if self.on_request:
                    self.on_request(portal_id)
                response = await self._client.send(request, stream=stream)
                jar.extract_cookies(response)
            if not follow_redirects or response.next_request is None:
                return response
            await response.aclose()
            request = response.next_request
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=request)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def public_search(self, portal_id: str, url: str, *, content: bytes, media_type: str,
                            cookies: httpx.Cookies, headers: dict[str, str] | None = None) -> httpx.Response:
        """Send one anonymous search with only the cookies (and anti-forgery headers) from its public page visit."""
        request = httpx.Request("POST", url, content=content, headers={
            **(headers or {}),
            "Content-Type": media_type, "User-Agent": USER_AGENT, "Accept": "application/json, text/html",
            "X-Requested-With": "XMLHttpRequest",
        })
        cookies.set_cookie_header(request)
        return await self._send(portal_id, request, follow_redirects=False)


class PortalFetcher:
    """A Fetcher bound to one portal. This is what brain/ functions receive."""

    def __init__(self, fetcher: Fetcher, portal_id: str) -> None:
        self.fetcher = fetcher
        self.portal_id = portal_id

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self.fetcher.get(self.portal_id, url, **kw)

    async def document_prefix(self, url: str) -> httpx.Response:
        """Read a bounded prefix even when a download server ignores Range."""
        request = self.fetcher._client.build_request("GET", url, headers={"Range": "bytes=0-16383"})
        response = await self.fetcher._send(self.portal_id, request, stream=True)
        try:
            prefix = bytearray()
            async for chunk in response.aiter_bytes(16384):
                prefix.extend(chunk)
                break
            return httpx.Response(response.status_code, content=bytes(prefix), request=response.request,
                                  headers={"Content-Type": response.headers.get("content-type", "")})
        finally:
            await response.aclose()

    async def public_search(self, url: str, *, content: bytes, media_type: str, cookies: httpx.Cookies,
                            headers: dict[str, str] | None = None) -> httpx.Response:
        return await self.fetcher.public_search(self.portal_id, url, content=content, media_type=media_type,
                                                cookies=cookies, headers=headers)

    async def stream(
        self, url: str, max_bytes: int, dest_dir: Path | None = None, *,
        validators: dict[str, str] | None = None, validator_target: str | None = None,
    ) -> Downloaded | TooLarge | NotModified:
        return await self.fetcher.stream(self.portal_id, url, max_bytes, dest_dir,
                                         validators=validators, validator_target=validator_target)

    async def wait_turn(self, url: str = "") -> None:
        """For Playwright: call this before letting the browser make a request."""
        await self.fetcher.wait_turn(self.portal_id, url)


class RunPageCache(PortalFetcher):
    """Replays GETs already answered in this run, so an attach's first read reuses the pages
    its validation just walked. Use for one run only: later reads must fetch fresh pages.
    Streams (documents) and public searches (POST) always go to the portal.
    """

    MAX_BYTES = 64 * 1024 * 1024  # shortcut: stops caching past this; enough for 100 list pages

    def __init__(self, fetcher: Fetcher, portal_id: str) -> None:
        super().__init__(fetcher, portal_id)
        self._pages: dict[tuple[str, str], httpx.Response] = {}
        self._bytes = 0
        self.scanned_pages: dict[str, int] = {}

    async def get(self, url: str, **kw) -> httpx.Response:
        key = (url, repr(sorted(kw.items())))
        if (cached := self._pages.get(key)) is not None:
            if self.portal_id in self.fetcher._cancelled:
                raise PortalCancelled(self.portal_id)
            return cached
        response = await super().get(url, **kw)
        if response.status_code < 400 and self._bytes + len(response.content) <= self.MAX_BYTES:
            self._pages[key] = response
            self._bytes += len(response.content)
        return response


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
    disposition = Message()
    disposition["Content-Disposition"] = response.headers.get("content-disposition", "")
    fallback = None
    for key, value in disposition.get_params(header="content-disposition", failobj=[]):
        if key != "filename":
            continue
        if isinstance(value, tuple):
            return collapse_rfc2231_value(value)
        try:
            fallback = unquote(value, errors="strict")
        except UnicodeDecodeError:
            fallback = value
    return fallback or None
