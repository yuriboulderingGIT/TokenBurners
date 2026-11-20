"""HTML shrinker. OWNER: B1.

Raw pages are often over 100k tokens. Shrink before sending anything to the model.
"""

import re

_DROP = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>|<!--.*?-->", re.S | re.I)
_SPACES = re.compile(r"\s+")


def shrink(html: str, max_chars: int = 60_000) -> str:
    # TODO(B1): strip attributes we don't need, keep only 3-5 list rows, cap at ~15-20k tokens.
    html = _DROP.sub("", html)
    html = _SPACES.sub(" ", html)
    return html[:max_chars]
