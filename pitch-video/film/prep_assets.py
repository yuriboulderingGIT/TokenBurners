"""Copy the captured stills into assets/ at film sizes, crop where only part of a page is used, copy the sounds.

All page stills are 3840x2160 captures (1280x720 CSS at 3x); film copies are 3200x1800 JPEG, and every
region in build.py is given in the 3840x2160 capture's pixels.
"""

import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
CAP = HERE.parent / "capture"
IMG = HERE / "assets" / "img"
SFX = HERE / "assets" / "sfx"
IMG.mkdir(parents=True, exist_ok=True)
SFX.mkdir(parents=True, exist_ok=True)

STILLS = {
    "dash-empty": "attach/01-dashboard-empty.png",
    "integrating-a": "attach/burst/0032.jpg",
    "integrating-b": "attach/burst/0044.jpg",
    "serving": "attach/03-portal-after.png",
    "docs": "views-v1/docs-open.png",
    "healing": "repair/burst/0015.jpg",
    "repaired": "repair/02-portal-after.png",
    "failed": "captcha/02-portal-after.png",
    "run3": "realruns/dash-run3.png",
    "ezak": "realruns/ezak-run3.png",
}
for name, rel in STILLS.items():
    im = Image.open(CAP / rel).convert("RGB")
    assert im.size == (3840, 2160), (rel, im.size)
    im.resize((3200, 1800), Image.LANCZOS).save(IMG / f"{name}.jpg", quality=90)

CROPS = {  # name: (source, box in capture pixels)
    "mock-v1": ("repair/mock-v1.png", (0, 0, 1400, 880)),
    "mock-v2": ("repair/mock-v2.png", (0, 0, 1400, 880)),
    "mock-captcha": ("captcha/mock-captcha.png", (0, 0, 1700, 1069)),
    "activity": ("captcha/03-dashboard-after.png", (2600, 860, 3840, 2160)),
}
for name, (rel, box) in CROPS.items():
    Image.open(CAP / rel).convert("RGB").crop(box).save(IMG / f"{name}.jpg", quality=92)

# Typing: only the address field changes, so keep a strip per frame (box in capture pixels).
TYPE_BOX = (2160, 30, 3800, 210)
frames = sorted((CAP / "attach" / "burst").glob("*.jpg"))[:30]
(IMG / "typing").mkdir(exist_ok=True)
for i, f in enumerate(frames):
    Image.open(f).convert("RGB").crop(TYPE_BOX).save(IMG / "typing" / f"{i:02d}.jpg", quality=92)

for f in sorted((CAP / "portals").glob("*.png")):
    Image.open(f).convert("RGB").resize((1440, 900), Image.LANCZOS).save(IMG / f"portal-{f.stem}.jpg", quality=88)

LIB = Path.home() / ".claude" / "skills" / "media-use" / "audio" / "assets" / "sfx"
for name in ("click.mp3", "click-soft.mp3", "pop.mp3", "whoosh.mp3", "whoosh-short.mp3", "whoosh-cinematic.mp3",
             "glitch-1.mp3", "glitch-2.mp3", "typing.mp3", "key-press.mp3", "riser.mp3", "impact-bass-1.mp3",
             "CREDITS.md"):
    shutil.copy2(LIB / name, SFX / name)
print("stills", len(STILLS), "crops", len(CROPS), "typing", len(frames), "sfx ok")
