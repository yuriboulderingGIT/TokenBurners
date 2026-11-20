"""Recognize login and CAPTCHA pages that prevent a public read."""

import re


class Blocked(Exception):
    """The portal needs a login or captcha. The portal goes to `failed` with this reason."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind  # "login" | "captcha"
        super().__init__(f"{kind} required" + (f": {detail}" if detail else ""))


_PASSWORD = re.compile(r"<input\b[^>]*\btype\s*=\s*['\"]?password\b", re.I)
_CAPTCHA_WIDGET = re.compile(
    r"<[^>]+\b(?:id|class)\s*=\s*['\"][^'\"]*\b(?:g-recaptcha|h-captcha|cf-turnstile)\b",
    re.I,
)
_CHALLENGE_HEADING = re.compile(
    r"<(?:title|h1)\b[^>]*>[^<]*(?:captcha|not a robot|verify you are human|just a moment)",
    re.I,
)
_LOGIN_HEADING = re.compile(
    r"<(?:title|h1)\b[^>]*>[^<]*(?:sign[ -]?in|log[ -]?in|anmeld(?:en|ung)|connexion)",
    re.I,
)


def detect_block(html: str, status: int, *, content_available: bool = False) -> str | None:
    """Ignore incidental challenge markup when the successful response contains tender data."""
    if status == 401:
        return "login"
    if 200 <= status < 300 and content_available:
        return None

    body = html[:256_000]
    short_page = len(body) < 8_000 and len(re.findall(r"<a\b", body, flags=re.I)) < 3
    if _CAPTCHA_WIDGET.search(body) and (status >= 400 or short_page or _CHALLENGE_HEADING.search(body)):
        return "captcha"
    if _CHALLENGE_HEADING.search(body) and "/cdn-cgi/challenge-platform/" in body:
        return "captcha"
    if _PASSWORD.search(body) and (status >= 400 or short_page or _LOGIN_HEADING.search(body)):
        return "login"
    return None
