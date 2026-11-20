"""Login / captcha detection. OWNER: B1.

Note from the brief: a captcha on the list page while documents still download is NOT a block.
"""

class Blocked(Exception):
    """The portal needs a login or captcha. The portal goes to `failed` with this reason."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind  # "login" | "captcha"
        super().__init__(f"{kind} required" + (f": {detail}" if detail else ""))


_CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "cf-turnstile", "captcha")
_LOGIN_MARKERS = ('type="password"', "type='password'")


def detect_block(html: str, status: int) -> str | None:
    """Return "login", "captcha" or None."""
    # TODO(B1): make this smarter; these markers are a first guess.
    lower = html.lower()
    if status in (401, 403) and any(m in lower for m in _LOGIN_MARKERS):
        return "login"
    if any(m in lower for m in _CAPTCHA_MARKERS):
        return "captcha"
    if any(m in lower for m in _LOGIN_MARKERS):
        return "login"
    return None
