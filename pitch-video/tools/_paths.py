"""Shared paths for the capture tools, with no machine-specific paths.

REPO is the Watchtower repo (the folder with app/main.py): this folder's grandparent when the tools live in the repo
(<repo>/pitch-video/tools), or a sibling "TokenBurners" checkout when pitch-video sits next to the repo.
Set WATCHTOWER_REPO to override. PV is the pitch-video folder (holds .env with ELEVENLABS_API_KEY).
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PV = HERE.parent


def _repo() -> Path:
    if os.environ.get("WATCHTOWER_REPO"):
        return Path(os.environ["WATCHTOWER_REPO"]).resolve()
    for candidate in (PV.parent, PV.parent / "TokenBurners"):
        if (candidate / "app" / "main.py").exists():
            return candidate
    raise SystemExit("Watchtower repo not found: set WATCHTOWER_REPO")


REPO = _repo()
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(REPO / "data" / "playwright"))
