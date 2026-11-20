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
    r"<(?:title|h[12])\b[^>]*>[^<]*(?:captcha|not a robot|verify (?:that )?you are human|just a moment)",
    re.I,
)
_LOGIN_HEADING = re.compile(
    r"<(?:title|h[12])\b[^>]*>[^<]*(?:sign[ -]?in|log[ -]?in|anmeld(?:en|ung)|connexion)",
    re.I,
)
_ANCILLARY = re.compile(r"<(header|nav|aside|footer)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_FORM = re.compile(r"<form\b[^>]*>.*?</form\s*>", re.I | re.S)
_PUBLIC_CONTENT = re.compile(r"<(?:a\b[^>]*\bhref\s*=|article\b|tr\b)", re.I)
_IMAGE_ANSWER = re.compile(r"<input\b[^>]*\bname\s*=\s*['\"]?answer\b", re.I)


def detect_block(html: str, status: int, *, content_available: bool = False) -> str | None:
    """Ignore incidental challenge markup when the successful response contains tender data."""
    if status == 401:
        return "login"
    if 200 <= status < 300 and content_available:
        return None

    body = html[:256_000]
    short_gate = False
    if status < 400:
        body = _ANCILLARY.sub("", body)
        short_gate = (
            len(body) < 8_000
            and _PUBLIC_CONTENT.search(_FORM.sub("", body)) is None
        )
    challenge_heading = _CHALLENGE_HEADING.search(body)
    if _IMAGE_ANSWER.search(body) and re.search(r"what code is in the image\?", body, re.I) and re.search(
        r"testing whether you are a human visitor", body, re.I
    ):
        return "captcha"
    if _CAPTCHA_WIDGET.search(body) and (status >= 400 or challenge_heading or short_gate):
        return "captcha"
    if challenge_heading and "/cdn-cgi/challenge-platform/" in body:
        return "captcha"
    if _PASSWORD.search(body) and (status >= 400 or _LOGIN_HEADING.search(body) or short_gate):
        return "login"
    return None
