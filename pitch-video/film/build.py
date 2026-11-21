"""Write the Watchtower pitch film (index.html + compositions/*.html) from narration + word timings.

Timing source: words.json / sections.json from voice.py. Without them, timings are estimated from
narration.json (2.55 words/s) so the edit can be built and checked before the voice exists.
Every region below is in capture pixels (the 3840x2160 screenshots); see ../capture/boxes.json.
Run: python build.py
"""

import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMP = HERE / "compositions"
COMP.mkdir(exist_ok=True)
NARR = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))["sections"]

# ---------------------------------------------------------------- timings
if (HERE / "words.json").exists() and (HERE / "sections.json").exists():
    WORDS = json.loads((HERE / "words.json").read_text(encoding="utf-8"))
    SPANS = {s["id"]: s for s in json.loads((HERE / "sections.json").read_text(encoding="utf-8"))["sections"]}
    VOICE_END = max(s["end"] for s in SPANS.values())
    ESTIMATED = False
else:
    WORDS, SPANS, t = [], {}, 0.0
    for sec in NARR:
        t += sec["gap"]
        start = t
        for w in sec["text"].split():
            d = 0.36 + 0.025 * max(0, len(w) - 5)
            WORDS.append({"id": f"w{len(WORDS)}", "text": w, "start": round(t, 3), "end": round(t + d - 0.05, 3),
                          "section": sec["id"]})
            t += d + (0.22 if w[-1] in ".?!:" else 0.08 if w[-1] == "," else 0)
        SPANS[sec["id"]] = {"id": sec["id"], "start": round(start, 3), "end": round(t, 3)}
    VOICE_END, ESTIMATED = t, True

TAIL = 2.8  # hold on the end card after the last word
TOTAL = round(VOICE_END + TAIL, 2)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9'-]", "", s.lower().replace("’", "'"))


def at(section: str, phrase: str, which: str = "start") -> float:
    """Film time of the first word of `phrase` inside `section` (or its last word's end with which='end')."""
    toks = [norm(p) for p in phrase.split()]
    ws = [w for w in WORDS if w["section"] == section]
    for i in range(len(ws) - len(toks) + 1):
        if all(norm(ws[i + j]["text"]) == toks[j] for j in range(len(toks))):
            return ws[i]["start"] if which == "start" else ws[i + len(toks) - 1]["end"]
    raise SystemExit(f"anchor not found: {section!r} {phrase!r}")


ORDER = [s["id"] for s in NARR]
LEAD = 0.3
SCENE = {}
for i, sid in enumerate(ORDER):
    start = 0.0 if i == 0 else round(SPANS[sid]["start"] - LEAD, 3)
    SCENE[sid] = start
SCENE_END = {sid: (SCENE[ORDER[i + 1]] if i + 1 < len(ORDER) else TOTAL) for i, sid in enumerate(ORDER)}

# ---------------------------------------------------------------- look
INK, MUTED, FAINT = "#18181b", "#52525b", "#6b6b75"
ACCENT, VIOLET = "#6366f1", "#6d4fd8"
SERVING, HEALING, FAILED, INTEGRATING = "#10b981", "#f59e0b", "#ef4444", "#3b82f6"
W, H, X, Y = 1920, 1080, 0, 0           # the product view fills the frame
CAPW = 3840
S0 = W / CAPW                            # capture px -> frame px at zoom 1
KMAX = 3200 / 1920                       # 3200 px film stills stay sharp up to here


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def halo(box: list[int], color: str, radius: int = 999) -> tuple:
    """A soft glow in the element's own colour (for pills and fields)."""
    return (box, "halo", color, radius)


def mark(box: list[int], color: str = "#6366f1") -> tuple:
    """A highlighter swipe over text, tinted (multiply), drawn left to right."""
    return (box, "mark", color, 10)

BASE_CSS = f"""
@font-face {{ font-family: "Mona Sans"; src: url('assets/fonts/MonaSans-latin.woff2') format('woff2'); font-weight: 200 900; }}
#root {{ position: absolute; inset: 0; font-family: "Mona Sans", sans-serif; color: {INK}; }}
.win {{ position: absolute; left: {X}px; top: {Y}px; width: {W}px; height: {H}px; overflow: hidden; background: #f7f6fc; }}
.cam {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; }}
.cam img {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; }}
.scrim {{ position: absolute; left: 0; right: 0; bottom: 0; height: 250px; pointer-events: none;
  background: linear-gradient(to bottom, rgba(249,248,253,0) 0%, rgba(249,248,253,.80) 52%, rgba(249,248,253,.96) 100%); }}
.hl {{ position: absolute; opacity: 0; }}
.hl.mark {{ mix-blend-mode: multiply; }}
.full {{ position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; overflow: hidden; background: #fff; opacity: 0; }}
.full .bar {{ height: 64px; background: #ecebf3; display: flex; align-items: center; gap: 10px; padding: 0 28px; }}
.full .bar b {{ width: 14px; height: 14px; border-radius: 50%; background: #cfcdd9; display: inline-block; }}
.full .bar span {{ margin-left: 20px; background: #fff; border-radius: 999px; padding: 4px 26px; font-size: 28px; color: {MUTED}; }}
.full .page {{ position: absolute; left: 0; top: 64px; width: 1920px; height: 1016px; overflow: hidden; }}
.full .page img {{ position: absolute; left: 120px; top: 44px; width: 1300px; height: 732px; }}   /* v3: calmer size, with margins */
.card {{ position: absolute; background: rgba(255,255,255,.97); border-radius: 22px; opacity: 0;
  box-shadow: 0 30px 80px rgba(49,46,129,.18), 0 2px 8px rgba(24,24,27,.07); }}
.mono {{ font-family: "JetBrains Mono", monospace; }}
.pill {{ display: inline-flex; align-items: center; gap: 12px; border-radius: 999px; font-weight: 600; }}
.pill i {{ width: 14px; height: 14px; border-radius: 50%; display: inline-block; }}
.browser {{ position: absolute; border-radius: 18px; overflow: hidden; background: #fff; opacity: 0;
  box-shadow: 0 34px 90px rgba(49,46,129,.16), 0 3px 10px rgba(24,24,27,.06); }}
.browser .bar {{ height: 58px; background: #ecebf3; display: flex; align-items: center; gap: 10px; padding: 0 22px; }}
.browser .bar b {{ width: 13px; height: 13px; border-radius: 50%; background: #cfcdd9; display: inline-block; }}
.browser .bar span {{ margin-left: 18px; background: #fff; border-radius: 999px; padding: 4px 22px; font-size: 28px; color: {MUTED}; }}
.browser .page {{ position: relative; overflow: hidden; }}
.browser .page img {{ position: absolute; left: 0; top: 0; }}
"""


class Scene:
    def __init__(self, sid: str):
        self.sid, self.start, self.end = sid, SCENE[sid], SCENE_END[sid]
        self.html, self.js, self.css = [], [], []

    def t(self, absolute: float) -> float:
        return round(absolute - self.start, 3)

    def a(self, phrase: str, which: str = "start", section: str | None = None) -> float:
        return self.t(at(section or self.sid, phrase, which))

    def add(self, s: str) -> None:
        self.html.append(s)

    def tl(self, s: str) -> None:
        self.js.append(s)

    # -- product window
    def window(self, name: str, images: list[str], rings: dict[str, tuple] | None = None, hidden: bool = False) -> str:
        wid = f"{self.sid}-{name}"
        imgs = "".join(f'<img id="{wid}-{i}" src="assets/img/{src}" alt="" style="opacity:{1 if i == 0 else 0}">'
                       for i, src in enumerate(images))
        ring_html = ""
        self.kinds = getattr(self, "kinds", {})
        for rname, (box, kind, color, radius) in (rings or {}).items():
            rx, ry, rw, rh = box
            px, py = (7, 7) if kind == "halo" else (12, 5)
            if kind == "halo":
                look = f"border-radius:{radius}px;box-shadow:0 0 0 6px {rgba(color, .22)},0 0 34px 8px {rgba(color, .30)}"
            else:
                look = f"border-radius:{radius}px;background:{rgba(color, .17)}"
            ring_html += (f'<div class="hl {kind}" id="{wid}-r-{rname}" style="left:{rx * S0 - px:.1f}px;top:{ry * S0 - py:.1f}px;'
                          f'width:{rw * S0 + 2 * px:.1f}px;height:{rh * S0 + 2 * py:.1f}px;{look}"></div>')
            self.kinds[f"{wid}-r-{rname}"] = kind
        style = ' style="opacity:0"' if hidden else ""
        self.add(f'<div class="win" id="{wid}"{style}><div class="cam" id="{wid}-cam">{imgs}{ring_html}</div>'
                 f'<div class="scrim"></div></div>')   # v2: soft fade behind the subtitles
        self.tl(f'tl.set("#{wid}-cam", {{transformOrigin: "0 0", x: 0, y: 0, scale: 1}}, 0);')
        return wid

    @staticmethod
    def focus(region: list[int], fill: float = 0.72) -> tuple[float, float, float]:
        rx, ry, rw, rh = region
        k = min(fill * W / (rw * S0), fill * H / (rh * S0), KMAX)
        k = max(k, 1.0)
        x = W / 2 - (rx + rw / 2) * S0 * k
        y = H / 2 - (ry + rh / 2) * S0 * k
        x = min(0, max(W - W * k, x))
        y = min(0, max(H - H * k, y))
        return round(k, 4), round(x, 1), round(y, 1)

    def cam(self, wid: str, region: list[int] | None, t: float, dur: float = 0.6, fill: float = 0.72) -> None:
        k, x, y = (1.0, 0.0, 0.0) if region is None else self.focus(region, fill)
        self.tl(f'tl.to("#{wid}-cam", {{x: {x}, y: {y}, scale: {k}, duration: {dur}, ease: "power3.inOut"}}, {t});')

    def swap(self, wid: str, show: int, hide: list[int], t: float) -> None:
        self.tl(f'tl.set("#{wid}-{show}", {{opacity: 1}}, {t});')
        for h in hide:
            self.tl(f'tl.set("#{wid}-{h}", {{opacity: 0}}, {t});')

    def ring(self, wid: str, name: str, t_in: float, t_out: float | None = None) -> None:
        rid = f"#{wid}-r-{name}"
        if self.kinds[f"{wid}-r-{name}"] == "mark":   # highlighter: swipes in from the left
            self.tl(f'tl.fromTo("{rid}", {{opacity: 1, scaleX: 0, transformOrigin: "0% 50%"}}, '
                    f'{{opacity: 1, scaleX: 1, duration: 0.45, ease: "power2.out"}}, {t_in});')
        else:                                          # halo: settles onto the pill
            self.tl(f'tl.fromTo("{rid}", {{opacity: 0, scale: 1.18, transformOrigin: "50% 50%"}}, '
                    f'{{opacity: 1, scale: 1, duration: 0.4, ease: "power3.out"}}, {t_in});')
        if t_out is not None:
            self.tl(f'tl.to("{rid}", {{opacity: 0, duration: 0.2, ease: "power1.in"}}, {t_out});')

    def card_in(self, sel: str, t: float, dx: float = 50, dy: float = 0, dur: float = 0.5) -> None:
        self.tl(f'tl.fromTo("{sel}", {{opacity: 0, x: {dx}, y: {dy}}}, {{opacity: 1, x: 0, y: 0, duration: {dur}, ease: "power3.out"}}, {t});')

    def card_out(self, sel: str, t: float, dx: float = 40, dy: float = 0) -> None:
        self.tl(f'tl.to("{sel}", {{opacity: 0, x: {dx}, y: {dy}, duration: 0.3, ease: "power2.in"}}, {t});')

    def shake(self, sel: str, t: float) -> None:
        for j, dx in enumerate([9, -7, 5, 0]):
            self.tl(f'tl.to("{sel}", {{x: {dx}, duration: .045, ease: "none"}}, {t + j * 0.045:.3f});')

    def fade_in(self, sel: str, t: float, dur: float = 0.4) -> None:
        self.tl(f'tl.fromTo("{sel}", {{opacity: 0}}, {{opacity: 1, duration: {dur}, ease: "power2.out"}}, {t});')

    def fade_out(self, sel: str, t: float, dur: float = 0.3) -> None:
        self.tl(f'tl.to("{sel}", {{opacity: 0, duration: {dur}, ease: "power2.in"}}, {t});')

    def write(self) -> None:
        page = f"""<template>
  <style>{BASE_CSS}{''.join(self.css)}</style>
  <div id="root" data-composition-id="{self.sid}" data-width="1920" data-height="1080">
    {chr(10).join(self.html)}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    {chr(10).join('    ' + j for j in self.js).strip()}
    window.__timelines["{self.sid}"] = tl;
  </script>
</template>
"""
        (COMP / f"{self.sid}.html").write_text(page, encoding="utf-8")


SFX: list[dict] = []
SFX_LEN = {"click-soft.mp3": 0.36, "click.mp3": 0.36, "glitch-1.mp3": 0.9, "glitch-2.mp3": 0.9, "impact-bass-1.mp3": 2.1,
           "key-press.mp3": 0.4, "pop.mp3": 0.72, "riser.mp3": 2.1, "typing.mp3": 1.05, "whoosh-cinematic.mp3": 1.5,
           "whoosh-short.mp3": 0.57, "whoosh.mp3": 0.57}
SFX_TRIM = {"riser.mp3": 1.2, "typing.mp3": 0.4, "whoosh-cinematic.mp3": 1.7}   # skip each file's silent lead-in
# seconds from the (trimmed) clip start to its audible hit, measured from the samples
SFX_HIT = {"click-soft.mp3": 0.05, "click.mp3": 0.05, "glitch-1.mp3": 0.18, "glitch-2.mp3": 0.02, "impact-bass-1.mp3": 0.3,
           "key-press.mp3": 0.03, "pop.mp3": 0.12, "riser.mp3": 1.84, "typing.mp3": 0.05, "whoosh-cinematic.mp3": 0.85,
           "whoosh-short.mp3": 0.15, "whoosh.mp3": 0.15}
SFX_FADE = {"glitch-1.mp3": 0.25, "glitch-2.mp3": 0.25, "whoosh-cinematic.mp3": 0.5, "typing.mp3": 0.15}


SFX_GAIN = 0.7   # v2: every effect quieter (files in assets/sfx/soft are also de-harshed and peak-levelled to -6 dBFS)


def sfx(t: float, name: str, vol: float = 0.45) -> None:
    """Place a sound so its hit lands one frame after picture time t (light first, then sound)."""
    SFX.append({"t": round(max(0.0, t + 1 / 30 - SFX_HIT[name]), 3), "name": name, "vol": round(vol * SFX_GAIN, 3)})


# ---------------------------------------------------------------- boxes (capture px)
BOX = json.loads((HERE / "boxes.json").read_text(encoding="utf-8"))   # from tools/measure.py
P, D, DO = BOX["portal"], BOX["dashboard"], BOX["docs-open"]
FIELD = [D["field"][0] - 20, D["field"][1] - 10, D["add"][0] + D["add"][2] - D["field"][0] + 40, D["field"][3] + 20]
PILLS = json.loads((HERE / "pills.json").read_text(encoding="utf-8"))   # measured per page by prep_assets.py
STATE_COLOR = {"integrating": INTEGRATING, "serving": SERVING, "healing": HEALING, "failed": FAILED}


def pill(image: str, state: str) -> tuple:
    return halo(PILLS[image], STATE_COLOR[state])
HEAD = [40, 440, 1900, 420]          # title, pill, url and reason line, at about 2x
FIT = 0.98


def reason(width: int) -> list[int]:
    """The reason line under the title, as wide as its text (about 21 capture px per character)."""
    return [P["reason"][0] - 6, P["reason"][1], width, P["reason"][3]]


REASON = reason(2150)
RECIPE_META = P["meta-recipe"]
RECIPE_VIEW = [1400, 380, 2440, 640]  # top-right corner: address bar + "Recipe v2 (...)" in context
IDS = [P["ids-col"][0] - 10, P["rows"][1], 1100, 620]


# ================================================================ scenes
def hook() -> Scene:
    s = Scene("hook")
    # v2: four portals, named by country only (their real names are long and noisy)
    names = ["ie-etenders", "nl-tenderned", "fr-place", "de-nrw"]
    labels = ["Ireland", "Netherlands", "France", "Germany"]
    tw, th, gapx, gapy, lab = 568, 355, 56, 28, 58
    x0 = (1920 - (2 * tw + gapx)) / 2
    y0 = 46                                 # second row's labels end near y 900, clear of the subtitles
    s.css.append(f"""
#hook-grid {{ position: absolute; inset: 0; opacity: 0; }}
.tile {{ position: absolute; width: {tw}px; }}
.tile .shot {{ width: {tw}px; height: {th}px; border-radius: 18px; overflow: hidden; background: #fff;
  box-shadow: 0 24px 60px rgba(49,46,129,.14), 0 2px 6px rgba(24,24,27,.06); }}
.tile img {{ width: {tw}px; height: {th}px; display: block; }}
.tile .lab {{ margin-top: 14px; font-size: 28px; font-weight: 550; color: {MUTED}; text-align: center; }}
""")
    pos = []
    tiles = ""
    for i, (n, label) in enumerate(zip(names, labels)):
        x = x0 + (i % 2) * (tw + gapx)
        y = y0 + (i // 2) * (th + lab + gapy)
        pos.append((x, y))
        tiles += (f'<div class="tile" id="hook-t{i}" style="left:{x:.0f}px;top:{y:.0f}px"><div class="shot">'
                  f'<img src="assets/img/portal-{n}.jpg" alt=""></div><div class="lab">{label}</div></div>')
    s.add(f'<div id="hook-grid">{tiles}</div>')
    # fade in during the silence before the first word, then a slow push
    s.tl('tl.fromTo("#hook-grid", {opacity: 0}, {opacity: 1, duration: 1.0, ease: "power1.inOut"}, 0.1);')
    s.tl(f'tl.fromTo("#hook-grid", {{scale: 1.0, transformOrigin: "50% 45%"}}, {{scale: 1.04, duration: {s.end - s.start:.2f}, ease: "none"}}, 0);')
    # "change overnight": two portals swap places
    tc = s.a("change")
    (ax, ay), (bx, by) = pos[1], pos[2]
    s.tl(f'tl.to("#hook-t1", {{x: {bx - ax:.0f}, y: {by - ay:.0f}, duration: .55, ease: "power3.inOut"}}, {tc});')
    s.tl(f'tl.to("#hook-t2", {{x: {ax - bx:.0f}, y: {ay - by:.0f}, duration: .55, ease: "power3.inOut"}}, {tc});')
    sfx(at("hook", "change") + 0.1, "whoosh-short.mp3", 0.16)
    s.write()
    return s


def problem() -> Scene:
    s = Scene("problem")
    s.css.append(f"""
.line {{ position: absolute; left: 170px; width: 760px; font-size: 58px; font-weight: 650; letter-spacing: -0.02em;
  line-height: 1.12; opacity: 0; }}
#problem-code {{ left: 980px; top: 620px; width: 800px; padding: 26px 30px; }}
.browser {{ position: absolute; border-radius: 18px; overflow: hidden; background: #fff; opacity: 0;
  box-shadow: 0 34px 90px rgba(49,46,129,.16), 0 3px 10px rgba(24,24,27,.06); }}
.browser .bar {{ height: 58px; background: #ecebf3; display: flex; align-items: center; gap: 10px; padding: 0 22px; }}
.browser .bar b {{ width: 13px; height: 13px; border-radius: 50%; background: #cfcdd9; display: inline-block; }}
.browser .bar span {{ margin-left: 18px; background: #fff; border-radius: 999px; padding: 4px 22px; font-size: 28px; color: {MUTED}; }}
.browser .page {{ position: relative; overflow: hidden; }}
.browser .page img {{ position: absolute; left: 0; top: 0; }}
#problem-code .file {{ font-size: 28px; color: {FAINT}; margin-bottom: 10px; }}
#problem-code .row {{ font-size: 28px; line-height: 1.6; color: {INK}; white-space: nowrap; }}
#problem-code .row em {{ font-style: normal; color: {FAINT}; display: inline-block; width: 160px; }}
#problem-code .bad {{ color: {FAILED}; }}
#problem-zero {{ font-size: 30px; font-weight: 650; color: {FAILED}; margin-top: 10px; opacity: 0; }}
""")
    s.add(f'<div class="line" id="problem-l1" style="top:240px">A hand-written scraper for every portal.</div>')
    s.add(f'<div class="line" id="problem-l2" style="top:430px">Scrapers break silently.</div>')
    s.add(f'<div class="line" id="problem-l3" style="top:560px;color:{MUTED};font-weight:550">Nobody notices until a deadline is missed.</div>')
    s.add('<div class="browser" id="problem-br" style="left:980px;top:80px;width:800px;height:509px">'
          '<div class="bar"><b></b><b></b><b></b><span>localhost:8090/tenders</span></div>'
          '<div class="page" style="width:800px;height:451px">'
          '<img id="problem-v1" src="assets/img/mock-v1.jpg" style="width:800px;height:451px" alt="">'
          '<img id="problem-v2" src="assets/img/mock-v2.jpg" style="width:800px;height:451px;opacity:0" alt=""></div></div>')
    s.add('<div class="card mono" id="problem-code"><div class="file">scraper.py</div>'
          '<div class="row"><em>rows</em><span class="sel">//table[@id=\'results\']/tr</span></div>'
          '<div class="row"><em>title</em><span class="sel">//h1</span></div>'
          '<div class="row"><em>deadline</em><span class="sel">dt[\'Submission deadline\']</span></div>'
          '<div id="problem-zero">0 tenders found</div></div>')
    t1 = s.a("hand-written")
    s.card_in("#problem-br", 0.05, dx=0, dy=30)     # v3: no empty screen while "For a company like Patterno" is said
    s.card_in("#problem-code", 0.3, dx=0, dy=30)
    s.card_in("#problem-l1", t1, dx=-30)
    t2 = s.a("break")
    s.card_in("#problem-l2", t2, dx=-30)
    t3 = s.a("changes")
    s.tl(f'tl.set("#problem-v2", {{opacity: 1}}, {t3 + 0.25});')
    s.tl(f'tl.set("#problem-v1", {{opacity: 0}}, {t3 + 0.25});')
    s.shake("#problem-br", t3 + 0.2)
    s.tl(f'tl.to("#problem-code .sel", {{color: "{FAILED}", duration: .3}}, {t3 + 0.6});')
    s.tl(f'tl.fromTo("#problem-zero", {{opacity: 0, y: 8}}, {{opacity: 1, y: 0, duration: .35, ease: "power2.out"}}, {s.a("stops")});')
    sfx(at("problem", "changes") + 0.22, "glitch-2.mp3", 0.16)
    s.card_in("#problem-l3", s.a("nobody"), dx=-30)
    s.write()
    return s


def solution() -> Scene:
    s = Scene("solution")
    s.css.append(f"""
#solution-mask {{ position: absolute; left: 0; width: 1920px; top: 250px; height: 220px; overflow: hidden; text-align: center; }}
#solution-word {{ font-size: 176px; font-weight: 720; letter-spacing: -0.04em; line-height: 220px; }}
#solution-field {{ position: absolute; left: 460px; top: 520px; width: 1000px; height: 92px; border-radius: 999px; background: #fff;
  box-shadow: 0 20px 60px rgba(49,46,129,.14); display: flex; align-items: center; padding: 0 12px 0 40px; opacity: 0; }}
#solution-field span {{ flex: 1; font-size: 32px; color: {FAINT}; }}
#solution-field b {{ background: {INK}; color: #fff; border-radius: 999px; padding: 18px 34px; font-size: 28px; font-weight: 600; }}
#solution-chips {{ position: absolute; left: 0; width: 1920px; top: 690px; display: flex; justify-content: center; gap: 22px; }}
.chip {{ display: inline-flex; align-items: center; gap: 14px; background: #fff; border-radius: 999px; padding: 18px 30px;
  font-size: 32px; font-weight: 600; box-shadow: 0 14px 40px rgba(49,46,129,.12); opacity: 0; }}
.chip i {{ width: 16px; height: 16px; border-radius: 50%; display: inline-block; }}
""")
    s.add('<div id="solution-mask"><div id="solution-word">Watchtower</div></div>')
    s.add('<div id="solution-field"><span>Portal address, e.g. https://…/tenders</span><b>Add portal</b></div>')
    chips = [("learns", "Learns once", INTEGRATING), ("reads", "Reads for free", SERVING),
             ("notices", "Notices breaks", HEALING), ("repairs", "Repairs itself", VIOLET)]
    s.add('<div id="solution-chips">' + "".join(
        f'<div class="chip" id="solution-c{i}"><i style="background:{c}"></i>{label}</div>' for i, (_, label, c) in enumerate(chips)) + '</div>')
    tw = s.a("watchtower")
    s.tl(f'tl.fromTo("#solution-word", {{yPercent: 105}}, {{yPercent: 0, duration: .8, ease: "power4.out"}}, {min(0.2, max(0.05, tw - 0.35))});')
    sfx(at("solution", "watchtower") + 0.05, "whoosh.mp3", 0.2)   # v2: a short clean whoosh, not the cinematic one
    s.card_in("#solution-field", s.a("give"), dx=0, dy=24)
    for i, (word, _, _) in enumerate(chips):
        s.tl(f'tl.fromTo("#solution-c{i}", {{opacity: 0, y: 20, scale: .94}}, {{opacity: 1, y: 0, scale: 1, duration: .4, '
             f'ease: "back.out(1.6)"}}, {s.a(word)});')
    sfx(at("solution", "repairs"), "pop.mp3", 0.22)
    s.write()
    return s


def attach() -> Scene:
    s = Scene("attach")
    wid = s.window("w", ["dash-empty.jpg", "integrating-a.jpg", "integrating-b.jpg", "serving.jpg"],
                   rings={"field": halo(FIELD, ACCENT), "pill": pill("integrating-a.jpg", "integrating"),
                          "pill-serving": pill("serving.jpg", "serving")})
    # typing strips sit over the address field (capture box 2160,30 -> 3800,210), under the highlights
    strips = "".join(f'<img id="attach-ty{i}" src="assets/img/typing/{i:02d}.jpg" alt="" '
                     f'style="left:{2160 * S0:.1f}px;top:{30 * S0:.1f}px;width:{1640 * S0:.1f}px;height:{180 * S0:.1f}px;opacity:0">'
                     for i in range(30))
    s.html[-1] = s.html[-1].replace('<div class="hl', strips + '<div class="hl', 1)
    s.css.append(f"""
#attach-recipe {{ left: 900px; top: 130px; width: 840px; padding: 30px 34px 28px; }}
#attach-recipe h3 {{ font-size: 34px; font-weight: 700; margin: 0; }}
#attach-recipe .by {{ font-size: 28px; color: {FAINT}; margin: 4px 0 14px; }}
.rrow {{ display: flex; gap: 18px; align-items: baseline; padding: 8px 12px; border-radius: 10px; }}
.rrow em {{ font-style: normal; width: 160px; flex: none; font-size: 28px; font-weight: 600; color: {MUTED}; }}
.rrow span {{ font-size: 28px; color: {INK}; white-space: nowrap; }}
#attach-tested {{ margin-top: 14px; font-size: 30px; font-weight: 650; color: #047857; opacity: 0; display: flex; gap: 12px; align-items: center; }}
""")
    rows = [("rows", "rows", "//table[@id='results']/tr"), ("title", "title", "//h1"),
            ("buyer", "buyer", "dt 'Contracting authority'"), ("deadline", "deadline", "dt 'Submission deadline'"),
            ("documents", "documents", "a[href] .pdf .zip .doc .xls")]
    s.add('<div class="card" id="attach-recipe"><h3>Recipe v1</h3><div class="by">written by gpt-5-mini, $0.004</div>' +
          "".join(f'<div class="rrow" id="attach-r{i}"><em>{label}</em><span class="mono">{sel}</span></div>'
                  for i, (_, label, sel) in enumerate(rows)) +
          '<div id="attach-tested"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#047857" stroke-width="3" '
          'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>Tested on 3 tenders</div></div>')
    # 1. type the address and add it
    t_add = s.a("add")
    s.cam(wid, FIELD, max(0.1, t_add - 0.7), dur=0.7, fill=0.85)
    for i in range(30):
        s.tl(f'tl.set("#attach-ty{i}", {{opacity: 1}}, {t_add + i * 0.045:.3f});')
        if i:
            s.tl(f'tl.set("#attach-ty{i - 1}", {{opacity: 0}}, {t_add + i * 0.045:.3f});')
    sfx(at("attach", "add"), "typing.mp3", 0.18)
    t_click = round(t_add + 30 * 0.045 + 0.15, 3)
    s.ring(wid, "field", t_add, t_click + 0.1)
    sfx(s.start + t_click, "click.mp3", 0.3)
    # 2. explores: the portal page, integrating
    t_exp = s.a("explores")
    s.swap(wid, 1, [0], t_exp - 0.15)
    s.tl(f'tl.set("#attach-ty29", {{opacity: 0}}, {t_exp - 0.15});')
    s.cam(wid, HEAD, t_exp - 0.15, dur=0.55, fill=FIT)
    s.ring(wid, "pill", t_exp + 0.2, s.a("recipe") + 0.2)
    sfx(at("attach", "explores") - 0.2, "whoosh-short.mp3", 0.15)
    # 3. the recipe, line by line as it is named
    t_rec = s.a("recipe")
    s.swap(wid, 2, [1], t_rec)
    s.cam(wid, [0, 380, 2400, 700], t_rec - 0.1, dur=0.6, fill=1.0)
    s.card_in("#attach-recipe", t_rec - 0.05)
    for i, word in enumerate(["rows", "title", "buyer", "deadline", "documents"]):
        t_w = s.a(word)
        s.tl(f'tl.to("#attach-r{i}", {{backgroundColor: "rgba(99,102,241,.12)", duration: .2}}, {t_w});')
        if i:
            s.tl(f'tl.to("#attach-r{i - 1}", {{backgroundColor: "rgba(99,102,241,0)", duration: .2}}, {t_w});')
    t_test = s.a("tested")
    s.tl(f'tl.to("#attach-r4", {{backgroundColor: "rgba(99,102,241,0)", duration: .2}}, {t_test});')
    s.tl(f'tl.fromTo("#attach-tested", {{opacity: 0, y: 8}}, {{opacity: 1, y: 0, duration: .35, ease: "power2.out"}}, {t_test});')
    # 4. serving
    t_green = s.a("green")
    s.card_out("#attach-recipe", t_green - 0.3)
    s.swap(wid, 3, [2], t_green)
    s.cam(wid, PILLS["serving.jpg"], t_green - 0.05, dur=0.6, fill=0.5)
    s.ring(wid, "pill-serving", s.a("serving"))
    sfx(at("attach", "serving"), "pop.mp3", 0.26)
    s.write()
    return s


def docs() -> Scene:
    s = Scene("docs")
    files = DO["files"]
    wid = s.window("w", ["docs.jpg"], rings={"files": mark([files[0] - 10, files[1] - 60, files[2] + 20, files[3] + 70]),
                                             "deadline": mark([DO["deadline"][0] + 10, DO["deadline"][1] + 200, DO["deadline"][2] - 30, 100])})
    s.css.append(f"""
#docs-tz {{ left: 250px; top: 680px; padding: 22px 30px; font-size: 32px; font-weight: 550; color: {INK}; }}
#docs-tz small {{ display: block; font-size: 28px; font-weight: 500; color: {FAINT}; margin-bottom: 4px; }}
""")
    s.add('<div class="card" id="docs-tz"><small>The portal says</small>Deadlines are local time in Europe/Dublin.</div>')
    t_docs = s.a("documents")
    # v2: framed a little higher so the page footer stays out of the subtitle area
    s.cam(wid, [files[0] - 1100, 1000, 1700, 600], max(0.1, t_docs - 0.5), dur=0.7, fill=0.9)
    s.ring(wid, "files", t_docs, s.a("deadlines") - 0.1)
    t_dl = s.a("deadlines")
    s.cam(wid, [DO["deadline"][0] - 1500, 1000, 2300, 600], t_dl - 0.2, dur=0.6, fill=0.95)
    s.ring(wid, "deadline", t_dl + 0.2)
    s.card_in("#docs-tz", t_dl + 0.3, dx=0, dy=24)
    s.write()
    return s


def repair() -> Scene:
    s = Scene("repair")
    s.add('<div class="full" id="repair-br"><div class="bar"><b></b><b></b><b></b><span>localhost:8090/tenders</span></div>'
          '<div class="page"><img id="repair-v1" src="assets/img/mock-v1.jpg" alt="">'
          '<img id="repair-v2" src="assets/img/mock-v2.jpg" style="opacity:0" alt=""></div><div class="scrim"></div></div>')
    wid = s.window("w", ["healing.jpg", "repaired.jpg"], rings={
        "pill": pill("healing.jpg", "healing"), "pill-serving": pill("repaired.jpg", "serving"),
        "reason": mark(reason(1560), HEALING), "ids": mark(IDS, SERVING)}, hidden=True)
    s.css.append(f"""
#repair-diff {{ left: 860px; top: 90px; width: 880px; padding: 28px 34px; }}
#repair-diff h3 {{ font-size: 34px; font-weight: 700; margin: 0; }}
#repair-diff .by {{ font-size: 28px; color: {FAINT}; margin: 4px 0 10px; }}
.drow {{ margin: 8px 0 12px; }}
.drow em {{ font-style: normal; font-size: 28px; font-weight: 600; color: {MUTED}; display: block; margin-bottom: 4px; }}
.drow .old, .drow .new {{ font-size: 28px; line-height: 1.3; word-break: break-all; padding: 3px 10px; border-radius: 8px; margin-top: 4px; }}
.drow .old {{ color: #b91c1c; background: rgba(239,68,68,.08); text-decoration: line-through; }}
.drow .new {{ color: #047857; background: rgba(16,185,129,.10); opacity: 0; }}
""")
    diff = [("rows", "//table[@id='results']/tr", "//div[contains(@class,'results')]/div[contains(@class,'card')]"),
            ("tender ID", "dt 'Tender ID'", "th 'Identifier'"), ("deadline", "dt 'Submission deadline'", "th 'Closing date'")]
    s.add('<div class="card" id="repair-diff"><h3>Recipe v2</h3><div class="by">repaired by gpt-5, $0.017</div>' +
          "".join(f'<div class="drow"><em>{n}</em><div class="old mono">{o}</div><div class="new mono" id="repair-n{i}">{nw}</div></div>'
                  for i, (n, o, nw) in enumerate(diff)) +
          '<div id="repair-pass"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#047857" stroke-width="3" '
          'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>Tested on 3 tenders: v2 passes</div></div>')
    s.css.append("#repair-pass { margin-top: 10px; font-size: 30px; font-weight: 650; color: #047857; opacity: 0; display: flex; gap: 12px; align-items: center; }")
    # the layout changes (the portal page fills the frame)
    s.fade_in("#repair-br", 0.0, dur=0.3)
    t_lay = s.a("layout")
    s.tl(f'tl.set("#repair-v2", {{opacity: 1}}, {t_lay + 0.2});')
    s.tl(f'tl.set("#repair-v1", {{opacity: 0}}, {t_lay + 0.2});')
    s.shake("#repair-br .page", t_lay + 0.15)
    sfx(at("repair", "layout") + 0.17, "glitch-1.mp3", 0.16)
    # the next read fails -> healing
    t_next = s.a("next")
    s.fade_in(f"#{wid}", t_next - 0.15, dur=0.35)
    s.fade_out("#repair-br", t_next + 0.2, dur=0.01)
    sfx(at("repair", "next") - 0.15, "whoosh.mp3", 0.16)
    s.tl(f'tl.set("#{wid}-cam", {{x: 0, y: 0, scale: 1}}, {t_next - 0.1});')
    s.cam(wid, HEAD, t_next + 0.2, dur=0.6, fill=FIT)
    s.ring(wid, "reason", s.a("fails"), s.a("healing") - 0.1)
    s.ring(wid, "pill", s.a("healing"), s.a("stronger"))
    # the stronger model rewrites the recipe
    t_str = s.a("stronger")
    s.cam(wid, [0, 380, 2300, 700], t_str - 0.1, dur=0.5, fill=1.0)
    s.card_in("#repair-diff", t_str)
    t_rw = s.a("rewrites")
    for i in range(3):
        s.tl(f'tl.fromTo("#repair-n{i}", {{opacity: 0, x: -10}}, {{opacity: 1, x: 0, duration: .3, ease: "power2.out"}}, {t_rw + 0.15 + i * 0.25:.2f});')
    # version two passes (on the card), serving again, same IDs
    s.tl(f'tl.fromTo("#repair-pass", {{opacity: 0, y: 8}}, {{opacity: 1, y: 0, duration: .35, ease: "power2.out"}}, {s.a("version")});')
    t_srv = s.a("serving")
    s.card_out("#repair-diff", t_srv - 0.2)
    s.swap(wid, 1, [0], t_srv - 0.2)
    s.cam(wid, HEAD, t_srv - 0.2, dur=0.5, fill=FIT)
    s.ring(wid, "pill-serving", t_srv + 0.2, s.a("same") - 0.1)
    sfx(at("repair", "serving") + 0.2, "pop.mp3", 0.26)
    t_same = s.a("same")
    s.cam(wid, [0, IDS[1] - 160, 2600, 860], t_same - 0.2, dur=0.6, fill=1.0)
    s.ring(wid, "ids", t_same + 0.2)
    s.write()
    return s


def captcha() -> Scene:
    s = Scene("captcha")
    s.add('<div class="full" id="captcha-br"><div class="bar"><b></b><b></b><b></b><span>localhost:8090/tenders</span></div>'
          '<div class="page"><img src="assets/img/mock-captcha.jpg" alt=""></div><div class="scrim"></div></div>')
    wid = s.window("w", ["failed.jpg"], rings={"pill": pill("failed.jpg", "failed"), "reason": mark(reason(560), FAILED)},
                   hidden=True)
    s.fade_in("#captcha-br", 0.0, dur=0.3)
    sfx(s.start + 0.05, "whoosh-short.mp3", 0.14)
    t_pre = s.a("pretend")
    s.fade_in(f"#{wid}", t_pre - 0.25, dur=0.35)
    s.fade_out("#captcha-br", t_pre + 0.15, dur=0.01)
    s.cam(wid, HEAD, t_pre + 0.1, dur=0.6, fill=FIT)
    s.ring(wid, "pill", s.a("failed"))
    s.ring(wid, "reason", s.a("why") - 0.15)
    sfx(at("captcha", "failed"), "pop.mp3", 0.22)
    s.write()
    return s


def how() -> Scene:
    s = Scene("how")
    nodes = [("first", "Learn", "An OpenAI model writes the recipe, once", "gpt-5-mini", INTEGRATING),
             ("second", "Read", "Plain Python code, no model calls", "every 15 minutes", SERVING),
             ("third", "Check", "A health check after every read", "tenders, files, IDs", ACCENT),
             ("fourth", "Repair", "The stronger model fixes the recipe", "gpt-5", HEALING)]
    nw, gap, nh = 370, 50, 320          # v3: back to v1's sizes
    x0 = (1920 - (4 * nw + 3 * gap)) / 2
    y0 = 300
    s.css.append(f"""
#how-title {{ position: absolute; left: 0; width: 1920px; top: 150px; text-align: center; font-size: 54px; font-weight: 680;
  letter-spacing: -0.02em; opacity: 0; }}
.node {{ position: absolute; top: {y0}px; width: {nw}px; height: {nh}px; background: #fff; border-radius: 26px; padding: 34px 32px;
  box-shadow: 0 24px 70px rgba(49,46,129,.14), 0 2px 8px rgba(24,24,27,.06); opacity: 0; }}
.node .n {{ display: flex; align-items: center; gap: 14px; font-size: 46px; font-weight: 720; letter-spacing: -0.02em; }}
.node .n i {{ width: 18px; height: 18px; border-radius: 50%; display: inline-block; }}
.node p {{ font-size: 28px; line-height: 1.3; color: {MUTED}; margin: 18px 0 0; }}
.node small {{ position: absolute; left: 32px; bottom: 26px; font-size: 28px; color: {FAINT}; }}
#how-svg {{ position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; overflow: visible; }}
""")
    s.add('<div id="how-title">How it works</div>')
    s.add("".join(f'<div class="node" id="how-n{i}" style="left:{x0 + i * (nw + gap):.0f}px"><div class="n"><i style="background:{c}"></i>{t}</div>'
                  f'<p>{p}</p><small>{sm}</small></div>' for i, (_, t, p, sm, c) in enumerate(nodes)))
    arrows = ""
    for i in range(3):
        xa, xb, ya = x0 + i * (nw + gap) + nw + 8, x0 + (i + 1) * (nw + gap) - 8, y0 + nh / 2
        arrows += (f'<path id="how-a{i}" d="M{xa:.0f} {ya} L{xb - 10:.0f} {ya}" stroke="{ACCENT}" stroke-width="4" fill="none" '
                   f'stroke-linecap="round" stroke-dasharray="80" stroke-dashoffset="80"/>'
                   f'<path id="how-h{i}" d="M{xb - 22:.0f} {ya - 11} L{xb - 8:.0f} {ya} L{xb - 22:.0f} {ya + 11}" stroke="{ACCENT}" '
                   f'stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0"/>')
    # loop: Repair back to Read, under the nodes. v2: the curve scoops in from the right, and the arrowhead
    # is built from the curve's real end direction so it sits on the line.
    xr, xd, yb = x0 + 3 * (nw + gap) + nw / 2, x0 + (nw + gap) + nw / 2, y0 + nh
    p0, p1, p2, p3 = (xr, yb + 10), (xr, yb + 150), (xd + 190, yb + 150), (xd + 12, yb + 16)
    loop = f"M{p0[0]:.0f} {p0[1]:.0f} C{p1[0]:.0f} {p1[1]:.0f}, {p2[0]:.0f} {p2[1]:.0f}, {p3[0]:.0f} {p3[1]:.0f}"
    pts = [tuple(((1 - u) ** 3) * a + 3 * ((1 - u) ** 2) * u * b + 3 * (1 - u) * u * u * c + u ** 3 * d
                 for a, b, c, d in zip(p0, p1, p2, p3)) for u in [i / 200 for i in range(201)]]
    length = sum(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))
    tx, ty = p3[0] - p2[0], p3[1] - p2[1]                 # end tangent (direction of travel)
    norm_t = (tx * tx + ty * ty) ** 0.5
    tx, ty = tx / norm_t, ty / norm_t
    wing = []
    for sign in (1, -1):                                  # two wings, 32 degrees off the backwards direction
        ang = math.radians(32) * sign
        bx, by = -tx, -ty
        wx, wy = bx * math.cos(ang) - by * math.sin(ang), bx * math.sin(ang) + by * math.cos(ang)
        wing.append((p3[0] + 24 * wx, p3[1] + 24 * wy))
    head = f"M{wing[0][0]:.1f} {wing[0][1]:.1f} L{p3[0]:.1f} {p3[1]:.1f} L{wing[1][0]:.1f} {wing[1][1]:.1f}"
    arrows += (f'<path id="how-loop" d="{loop}" stroke="{HEALING}" stroke-width="4" fill="none" stroke-linecap="round" '
               f'stroke-dasharray="{length + 4:.0f}" stroke-dashoffset="{length + 4:.0f}"/>'
               f'<path id="how-loophead" d="{head}" stroke="{HEALING}" '
               f'stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0"/>'
               f'<text id="how-looplabel" x="{(xr + xd) / 2 + 60:.0f}" y="{yb + 160}" text-anchor="middle" font-size="30" fill="{MUTED}" '
               f'font-family="Mona Sans" opacity="0">when a portal changes</text>')
    s.add(f'<svg id="how-svg" viewBox="0 0 1920 1080">{arrows}</svg>')
    s.fade_in("#how-title", 0.1)
    for i, (word, *_rest) in enumerate(nodes):
        t_n = s.a(word)
        s.tl(f'tl.fromTo("#how-n{i}", {{opacity: 0, y: 24}}, {{opacity: 1, y: 0, duration: .45, ease: "power3.out"}}, {t_n});')
        if i:
            s.tl(f'tl.to("#how-a{i - 1}", {{strokeDashoffset: 0, duration: .35, ease: "power2.out"}}, {t_n - 0.2});')
            s.tl(f'tl.to("#how-h{i - 1}", {{opacity: 1, duration: .15}}, {t_n + 0.1});')
    t_loop = s.a("repairs")
    s.tl(f'tl.to("#how-loop", {{strokeDashoffset: 0, duration: .8, ease: "power2.inOut"}}, {t_loop - 0.3});')
    s.tl(f'tl.to("#how-loophead", {{opacity: 1, duration: .1}}, {t_loop + 0.48});')   # as the line arrives
    s.tl(f'tl.to("#how-looplabel", {{opacity: 1, duration: .3}}, {t_loop + 0.2});')
    s.write()
    return s


def real() -> Scene:
    s = Scene("real")
    wid = s.window("w", ["ezak.jpg", "run3.jpg"], rings={
        "ezak": mark(reason(1250), SERVING),
        "deliver": mark([664, 356, 615, 58], ACCENT)})
    s.cam(wid, HEAD, 0.0, dur=0.01, fill=FIT)
    s.ring(wid, "ezak", 0.5, s.a("which") - 0.1)
    t_which = s.a("which")
    s.swap(wid, 1, [0], t_which - 0.2)
    s.tl(f'tl.set("#{wid}-cam", {{x: 0, y: 0, scale: 1}}, {t_which - 0.2});')
    s.cam(wid, [40, 260, 2300, 580], t_which - 0.15, dur=0.5, fill=1.0)
    s.ring(wid, "deliver", t_which + 0.25, s.a("others") - 0.1)
    t_oth = s.a("others")
    s.cam(wid, [40, 1000, 2600, 700], t_oth - 0.15, dur=0.6, fill=1.0)
    sfx(s.start + t_which - 0.2, "whoosh-short.mp3", 0.14)
    s.write()
    return s


def costs() -> Scene:
    s = Scene("costs")
    tiles = [("portal", "Learning a portal", "1–2¢", "measured $0.004 to $0.019"),
             ("repair", "A repair", "2–3¢", "measured $0.017 to $0.028"),
             ("read", "A regular read", "0¢", "no model calls")]
    tw, gap = 470, 44                   # v3: back to v1's sizes
    x0 = (1920 - (3 * tw + 2 * gap)) / 2
    s.css.append(f"""
.stat {{ position: absolute; top: 290px; width: {tw}px; height: 400px; background: #fff; border-radius: 28px; padding: 44px 42px;
  box-shadow: 0 24px 70px rgba(49,46,129,.14), 0 2px 8px rgba(24,24,27,.06); opacity: 0; }}
.stat .l {{ font-size: 32px; font-weight: 600; color: {MUTED}; }}
.stat .v {{ font-size: 140px; font-weight: 720; letter-spacing: -0.04em; margin-top: 26px; line-height: 1; }}
.stat .s {{ position: absolute; left: 42px; bottom: 38px; font-size: 28px; color: {FAINT}; }}
""")
    s.add("".join(f'<div class="stat" id="costs-s{i}" style="left:{x0 + i * (tw + gap):.0f}px"><div class="l">{l}</div>'
                  f'<div class="v">{v}</div><div class="s">{sm}</div></div>' for i, (_, l, v, sm) in enumerate(tiles)))
    for i, (word, *_r) in enumerate(tiles):
        t_w = s.a(word) if word != "read" else s.a("regular")
        t_w = 0.15 if i == 0 else t_w - 0.15   # v3: the first tile is there as the scene opens
        s.tl(f'tl.fromTo("#costs-s{i}", {{opacity: 0, y: 34}}, {{opacity: 1, y: 0, duration: .5, ease: "power3.out"}}, {max(0.05, t_w):.2f});')
    sfx(at("costs", "nothing"), "pop.mp3", 0.2)
    s.write()
    return s


def honest() -> Scene:
    s = Scene("honest")
    s.css.append(f"""
#honest-box {{ position: absolute; left: 330px; top: 170px; width: 1260px; height: 250px; border: 3px dashed #c7c4e6; border-radius: 30px;
  opacity: 0; }}
#honest-box .cap {{ position: absolute; left: 40px; top: -24px; background: #f3f1fb; padding: 0 16px; font-size: 30px; font-weight: 650; }}
#honest-box .row {{ position: absolute; left: 40px; right: 40px; top: 74px; display: flex; gap: 18px; justify-content: center; }}
#honest-box .row span {{ background: #fff; border-radius: 16px; padding: 22px 28px; font-size: 30px; font-weight: 600;
  box-shadow: 0 12px 34px rgba(49,46,129,.10); }}
#honest-polite {{ position: absolute; left: 0; width: 1920px; top: 470px; text-align: center; font-size: 36px; color: {MUTED}; opacity: 0; }}
#honest-states {{ position: absolute; left: 0; width: 1920px; top: 590px; display: flex; justify-content: center; gap: 26px; }}
.state {{ display: inline-flex; align-items: center; gap: 16px; border-radius: 999px; padding: 22px 40px; font-size: 40px; font-weight: 650; opacity: 0; }}
.state i {{ width: 18px; height: 18px; border-radius: 50%; display: inline-block; }}
""")
    s.add('<div id="honest-box"><div class="cap">One Docker container</div><div class="row">'
          '<span>FastAPI</span><span>Dashboard</span><span>Reader + health check</span><span>SQLite</span><span>Playwright</span></div></div>')
    s.add('<div id="honest-polite">About one request per second to any portal</div>')
    states = [("serving", SERVING, "#ecfdf5", "#047857"), ("healing", HEALING, "#fffbeb", "#b45309"), ("failed", FAILED, "#fef2f2", "#b91c1c")]
    s.add('<div id="honest-states">' + "".join(
        f'<div class="state" id="honest-s{i}" style="background:{bg};color:{fg}"><i style="background:{c}"></i>{n}</div>'
        for i, (n, c, bg, fg) in enumerate(states)) + '</div>')
    s.card_in("#honest-box", 0.1, dx=0, dy=24)   # v3: there as the scene opens
    s.fade_in("#honest-polite", s.a("polite"))
    t_h = s.a("honest")
    for i in range(3):
        s.tl(f'tl.fromTo("#honest-s{i}", {{opacity: 0, y: 18, scale: .95}}, {{opacity: 1, y: 0, scale: 1, duration: .4, '
             f'ease: "back.out(1.5)"}}, {t_h + i * 0.18:.2f});')
    s.write()
    return s


def end() -> Scene:
    s = Scene("end")
    s.css.append(f"""
#end-mask {{ position: absolute; left: 0; width: 1920px; top: 300px; height: 210px; overflow: hidden; text-align: center; }}
#end-word {{ font-size: 168px; font-weight: 720; letter-spacing: -0.04em; line-height: 210px; }}
#end-by {{ position: absolute; left: 0; width: 1920px; top: 540px; text-align: center; font-size: 40px; font-weight: 600; opacity: 0; }}
#end-meta {{ position: absolute; left: 0; width: 1920px; top: 610px; text-align: center; font-size: 28px; color: {MUTED}; opacity: 0; }}
#end-link {{ position: absolute; left: 0; width: 1920px; top: 700px; text-align: center; font-size: 28px; color: {VIOLET}; opacity: 0; }}
""")
    s.add('<div id="end-mask"><div id="end-word">Watchtower</div></div>')
    s.add('<div id="end-by">by TokenBurners</div>')
    s.add('<div id="end-meta">MAIN AI Hackathon, Maastricht 2026 · Patterno track</div>')
    s.add('<div id="end-link">github.com/yuriboulderingGIT/TokenBurners</div>')
    s.tl(f'tl.set("#end-word", {{yPercent: 105}}, 0);')
    s.fade_in("#end-by", 0.15)   # v3: no empty screen while "We are TokenBurners" is said
    t_w = s.a("watchtower")
    s.tl(f'tl.fromTo("#end-word", {{yPercent: 105}}, {{yPercent: 0, duration: .8, ease: "power4.out"}}, {t_w - 0.1});')
    s.fade_in("#end-meta", t_w + 0.6)
    s.fade_in("#end-link", t_w + 0.9)
    sfx(at("end", "watchtower") + 0.25, "riser.mp3", 0.14)
    sfx(at("end", "watchtower") + 0.25, "impact-bass-1.mp3", 0.24)
    s.write()
    return s


# ================================================================ captions
def captions() -> None:
    groups, cur = [], []
    for i, w in enumerate(WORDS):
        if cur and (len(cur) >= 5 or w["section"] != cur[-1]["section"] or cur[-1]["text"][-1] in ".?!:;"
                    or (cur[-1]["text"][-1] == "," and len(cur) >= 3) or w["start"] - cur[-1]["end"] > 0.35):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    html, js = [], []
    for gi, g in enumerate(groups):
        start = max(0.1, g[0]["start"] - 0.06)
        nxt = groups[gi + 1][0]["start"] - 0.06 if gi + 1 < len(groups) else g[-1]["end"] + 0.6
        end = min(g[-1]["end"] + 0.5, nxt)
        words = " ".join(f'<span id="cw{w["id"][1:]}">{w["text"]}</span>' for w in g)
        html.append(f'<div class="cg" id="cg{gi}"><div class="cb">{words}</div></div>')
        js.append(f'tl.set("#cg{gi}", {{visibility: "visible"}}, {start:.3f});')
        js.append(f'tl.fromTo("#cg{gi}", {{opacity: 0}}, {{opacity: 1, duration: .1, ease: "power1.out"}}, {start:.3f});')
        for w in g:
            js.append(f'tl.to("#cw{w["id"][1:]}", {{color: "{INK}", duration: .08}}, {w["start"]:.3f});')
        js.append(f'tl.to("#cg{gi}", {{opacity: 0, duration: .1, ease: "power2.in"}}, {end - 0.1:.3f});')
        js.append(f'tl.set("#cg{gi}", {{opacity: 0, visibility: "hidden"}}, {end:.3f});')
    page = f"""<template>
  <style>
    @font-face {{ font-family: "Mona Sans"; src: url('assets/fonts/MonaSans-latin.woff2') format('woff2'); font-weight: 200 900; }}
    #root {{ position: absolute; inset: 0; font-family: "Mona Sans", sans-serif; }}
    .cg {{ position: absolute; left: 0; width: 1920px; bottom: 104px; text-align: center; opacity: 0; visibility: hidden; overflow: visible; }}
    /* v3: v1's type (38 px, 600) without the box; a light glow, the screens fade out behind it (scrim) */
    .cb {{ display: inline-block; font-size: 38px; font-weight: 600; letter-spacing: -0.01em; color: #6e6e76;
      text-shadow: 0 0 1px #fff, 0 0 6px rgba(255,255,255,.9), 0 0 14px rgba(255,255,255,.7); }}
  </style>
  <div id="root" data-composition-id="captions" data-width="1920" data-height="1080">
    {chr(10).join(html)}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    {chr(10).join('    ' + j for j in js).strip()}
    window.__timelines["captions"] = tl;
  </script>
</template>
"""
    (COMP / "captions.html").write_text(page, encoding="utf-8")


# ================================================================ root
def root(scenes: list[Scene]) -> None:
    hosts = []
    for i, sc in enumerate(scenes):
        hosts.append(f'<div id="{sc.sid}" class="clip" data-composition-id="{sc.sid}" data-composition-src="compositions/{sc.sid}.html" '
                     f'data-start="{sc.start}" data-duration="{round(sc.end - sc.start, 3)}" data-track-index="1" '
                     f'data-track-kind="graphics" data-width="1920" data-height="1080" style="z-index:{2 + i}"></div>')
    hosts.append(f'<div id="captions" class="clip" data-composition-id="captions" data-composition-src="compositions/captions.html" '
                 f'data-start="0" data-duration="{TOTAL}" data-track-index="2" data-track-kind="captions" '
                 f'data-width="1920" data-height="1080" style="z-index:50"></div>')
    audio = []
    if (HERE / "assets" / "voice" / "voice.wav").exists():
        audio.append(f'<audio id="vo" class="clip" src="assets/voice/voice.wav" data-start="0" data-track-index="3" data-volume="1"></audio>')
    for i, fx in enumerate(sorted(SFX, key=lambda f: f["t"])):
        name = fx["name"]
        extra = ""
        if name in SFX_TRIM:
            extra += f' data-media-start="{SFX_TRIM[name]}"'
        if name in SFX_FADE:
            extra += f' data-fade-out="{SFX_FADE[name]}"'
        audio.append(f'<audio id="fx{i:02d}" class="clip" src="assets/sfx/soft/{name}" data-start="{fx["t"]:.3f}" '
                     f'data-duration="{SFX_LEN[name]}" data-track-index="{4 + i}" data-volume="{fx["vol"]}"{extra}></audio>')
    page = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="assets/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #f7f6fc; }}
      #ground {{ position: absolute; inset: 0;
        background: radial-gradient(1400px 820px at 10% -10%, #e2dcfa 0%, rgba(226,220,250,0) 70%),
                    radial-gradient(1200px 760px at 105% 110%, #e5e0fb 0%, rgba(229,224,251,0) 72%), #f3f1fb; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
      <div id="ground"></div>
      {(chr(10) + '      ').join(hosts + audio)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
    (HERE / "index.html").write_text(page, encoding="utf-8")


scenes = [hook(), problem(), solution(), attach(), docs(), repair(), captcha(), how(), real(), costs(), honest(), end()]
captions()
root(scenes)
print(f"{'ESTIMATED' if ESTIMATED else 'voice'} timings, total {TOTAL:.1f} s, {len(SFX)} sounds")
for sc in scenes:
    print(f"  {sc.sid:<9} {sc.start:6.2f} -> {sc.end:6.2f}  ({sc.end - sc.start:4.1f} s)")
