"""Copy the captured stills into assets/ at film sizes, crop where only part of a page is used, copy the sounds.

All page stills are 3840x2160 captures (1280x720 CSS at 3x); film copies are 3200x1800 JPEG, and every
region in build.py is given in the 3840x2160 capture's pixels.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

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

CROPS = {  # name: (source, box in capture pixels); mock pages are 16:9 so they can fill the frame
    "mock-v1": ("repair/mock-v1.png", (0, 0, 1500, 845)),
    "mock-v2": ("repair/mock-v2.png", (0, 0, 1500, 845)),
    "mock-captcha": ("captcha/mock-captcha.png", (0, 0, 1500, 845)),
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
    tile = Image.open(f).convert("RGB").resize((1440, 900), Image.LANCZOS)
    # v3: a gentle calm-down of the busy government sites (less saturated, slightly softer contrast)
    tile = ImageEnhance.Contrast(ImageEnhance.Color(tile).enhance(0.72)).enhance(0.94)
    tile.save(IMG / f"portal-{f.stem}.jpg", quality=88)

LIB = Path.home() / ".claude" / "skills" / "media-use" / "audio" / "assets" / "sfx"
if LIB.exists():
    for name in ("click.mp3", "click-soft.mp3", "pop.mp3", "whoosh.mp3", "whoosh-short.mp3", "whoosh-cinematic.mp3",
                 "glitch-1.mp3", "glitch-2.mp3", "typing.mp3", "key-press.mp3", "riser.mp3", "impact-bass-1.mp3",
                 "CREDITS.md"):
        shutil.copy2(LIB / name, SFX / name)

# Softer copies: tame the harsh highs (-7 dB shelf above 4 kHz) and level every peak to -6 dBFS,
# so data-volume alone sets how loud each sound sits under the voice.
FFMPEG = shutil.which("ffmpeg") or str(next(Path.home().glob(
    "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin/ffmpeg.exe")))
(SFX / "soft").mkdir(exist_ok=True)
for src in SFX.glob("*.mp3"):
    tmp = SFX / "soft" / f"{src.stem}.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", "highshelf=f=4000:g=-7,lowpass=f=11000",
                    "-ar", "48000", str(tmp)], check=True)
    peak = re.search(r"max_volume: (-?[\d.]+) dB", subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(tmp), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True).stderr)
    gain = -6.0 - float(peak.group(1)) if peak else 0.0
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(tmp), "-af", f"volume={gain:.2f}dB", "-b:a", "192k",
                    str(SFX / "soft" / src.name)], check=True)
    tmp.unlink()

# The status pill on each page, measured from the pixels (its tint), so a highlight fits it exactly.
PILL_SOURCES = {"integrating-a.jpg": "attach/burst/0032.jpg", "integrating-b.jpg": "attach/burst/0044.jpg",
                "serving.jpg": "attach/03-portal-after.png", "healing.jpg": "repair/burst/0015.jpg",
                "repaired.jpg": "repair/02-portal-after.png", "failed.jpg": "captcha/02-portal-after.png",
                "ezak.jpg": "realruns/ezak-run3.png"}
pills = {}
for name, rel in PILL_SOURCES.items():
    px = np.asarray(Image.open(CAP / rel).convert("RGB")).astype(int)
    x0, y0 = 560, 460
    crop = px[y0:620, x0:1500]
    ys, xs = np.nonzero((crop.max(axis=2) - crop.min(axis=2)) > 18)
    pills[name] = [int(x0 + xs.min()), int(y0 + ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
(HERE / "pills.json").write_text(json.dumps(pills, indent=1), encoding="utf-8")
print("stills", len(STILLS), "crops", len(CROPS), "typing", len(frames), "sfx ok, pills", pills)
