# Pitch video: everything needed for the edit

*Updated Saturday 21 Nov, 01:30, against repo `main` at `66444f9`.*

MAIN Summit Hackathon, Maastricht, Patterno track. **One video.** Edited in Claude Code (desktop app,
Code tab) with the `youtube-edit` skill.

**Max length: 2:30. Aim for 2:15–2:20** so there is room for the end card and nothing gets cut off.
Patterno's own limit is 3:00, so 2:30 is safe for both. That is about **320–350 spoken words**. The script in
`SCRIPT.md` has about 320.

**Who does what:** you record the live demo and speak over it. Claude Code edits.

Files in this folder:
- `SCRIPT.md`: the script, the shot list and the demo steps
- `BRIEF.md`: the brief to paste into the editing session (fill in the `___` first)
- `Claude editing skill/youtube-edit-skill-main/`: the downloaded skill (not installed yet)
- this file: setup, recording rules and the checklist

## Where we are (01:30)

| Item | State |
|---|---|
| Script, shot list, brief | ✓ updated to the current repo; costs now measured (README "Measured model cost") |
| Demo flow on the mock (attach → layout change → repair → captcha) | ✓ checked on `66444f9` with the offline proof |
| Editing skill | downloaded into this folder, **not installed** (no `.claude/skills/youtube-edit/`) |
| Live demo recording `raw/pitch.mp4` | **not recorded** (no `raw/` folder) |
| Logos `img/`, morning clip `demo/` | **not done** |
| Overnight run of real portals | **not confirmed**: the server on port 8080 is a 4-portal test. Decide below. |

---

## 1. Setup (now)

1. Open the Claude desktop app → **Code** tab → **Local** → choose this `pitch-video` folder.
2. Paste (adapted from the skill's own README):
   > Set up the video editing skill in `Claude editing skill/youtube-edit-skill-main` for me: install it at `.claude/skills/youtube-edit` in this folder with SKILL.md at its top level, then install HyperFrames, FFmpeg and Parakeet, run the skill's tests, and walk me through anything you can't do yourself.
3. Read and approve the commands it asks to run. The skill's README suggests turning permissions to auto; keep
   approving by hand at least for the install.
4. Restart the session, then ask "which skills can you see?" and check that `youtube-edit` is listed.
5. **Test run:** record a 30-second screen recording with your voice, one deliberate mistake and a
   restart. Put it in `raw/` and say "Use my editing skill to edit this hook: raw/<file>, render at 1080p".
   If this works, the real edit will work.
6. Switch the model to **Sonnet** for the editing to save usage.

The setup installs or checks: Node.js 22+, HyperFrames (via npx), FFmpeg, Python 3 + numpy, auto-editor,
Parakeet (the transcriber), Google Chrome. Expect 30–60 minutes the first time.

Before running it: skim the skill and check that it's the creator's real one. It runs code on your laptop.
If possible, edit on a different laptop from the one running Watchtower.

---

## 2. Recording (as soon as possible: it does not depend on any overnight run)

The main recording is a **screen recording of the live demo with your voice over it**, not a talking head.
`SCRIPT.md` has the exact demo steps and the commands (fresh data folder `data/demo`, mock portal, browser tabs).

**Credits:** each full take costs about **3–4 cents** in real model calls (attach ≈ $0.006, repair ≈ $0.03).
Use your own key, never the jury's hand-in key.

**Screen**
- **OBS Studio**: display capture + microphone, **the screen's full resolution (4K if available)**, 30 fps.
  The skill's own note: 1080p goes soft past ~112 % zoom, 4K holds 200 %. The edit zooms into small pills.
- Browser zoomed to **150 %** so text stays readable when the edit zooms in. Bookmarks bar hidden,
  other tabs closed.
- **Nothing private on screen:** no `.env`, no terminal showing a key, no email, no notifications
  (turn on Do Not Disturb).

**Sound (matters more than picture)**
- Quiet room, mic close to you. Wired earbuds or a headset beat a laptop mic.
- Leave **one second of silence** at the start and end.
- Stay silent while the app is working (attach, repair). The edit cuts silent waits.

**How to talk**
- Fluffed a line? **Pause about 2 seconds and restart the whole sentence from its first word.** The last take wins.
- Got a number or name wrong? Redo the full sentence.
- No spoken notes like "take two" or "cut that bit" (they count as speech). **Clap** instead.
- Say numbers in full: "one to two cents", not "$0.01".
- **Say tool names out loud** (OpenAI, Python, Docker) so logo cards can appear on the word.
- Say lists as lists ("first..., second..., third..., fourth...") so they can become a diagram.
- End on the closing line: "We are TokenBurners, and this is Watchtower."

**Story (2:20)**

| Part | Time | What |
|---|---|---|
| Hook | 0:00–0:10 | Tenders are spread over countless portals that change without warning |
| Problem | 0:10–0:35 | Hand-written scrapers break silently |
| Solution | 0:35–0:55 | Watchtower: give it an address; it learns, reads, notices, repairs |
| Demo | 0:55–1:45 | Add portal → serving → documents → layout change → healing → serving → captcha → failed |
| How it works | 1:45–2:00 | Learn → Read → Check → Repair; OpenAI, Python |
| Impact + close | 2:00–2:20 | Real-portal dashboard, cost per portal, one Docker container, end card |

---

## 3. Files in the folder

```
pitch-video/
  raw/pitch.mp4                    ← the narrated live-demo recording, retakes included
  demo/01_dashboard_morning.mp4    ← 10–15 s of the dashboard with real portals (optional, see section 7)
  img/logo.svg (or .png)           ← Watchtower wordmark, transparent background
  img/tools/                       ← OpenAI, Python, Docker (+ FastAPI, Playwright, SQLite)
  img/screenshots/                 ← every number you say + 3 real portal list pages
  img/team.jpg                     ← optional, for the end card
  music/<track>.mp3                ← optional, only if you're allowed to use it
  BRIEF.md                         ← filled in
  SCRIPT.md
```

**Numbers:** only say numbers you can back up. The costs are measured and written in the repo README
("Measured model cost": attach $0.0055 on the mock and $0.019 on Cyprus, repair $0.028, read $0); screenshot that
section into `img/screenshots/`. A count of serving portals only goes in if the morning dashboard shows it.
Since 21 Nov a portal whose tenders have no documents shows *failed*, so "serving" means real delivery.

**Sound effects / music:** the skill ships with no sounds or music (its `SOUNDS.md` explains building a free
Pixabay library, about 20 minutes). Bring a track you're allowed to use, build the library, or use silence.

---

## 4. Information the edit needs

All of it is pre-filled in `BRIEF.md`, except:
1. Upload location, file size limit (MAIN submission page)
2. Team names and the repo link (only if the repo is public at hand-in)
3. The serving count, only if there is a morning dashboard to back it
4. Music: yes or no

---

## 5. Changes to the skill's defaults (already in BRIEF.md)

- **House style from our dashboard instead of the skill's example brand** (its sky blue, gold, rose and Manrope):
  violet #8b5cf6, indigo #6366f1, ink #18181b, background #f7f6fc; Inter for text, EB Garamond for titles.
- **Screen recording, not a face:** overlays must not cover the UI being talked about.
- **Render 1080p, 30 fps** instead of the skill's 4K default.
- **End card allowed:** product name, team, link.
- **No YouTube parts:** no subscribe bar (format 14), no "link in the description" pointer, no chapter list.
- **Captions on** (format 47, word-by-word) so judges can follow without sound.
- **Hard limit 2:30.** If the cut runs long, it suggests lines to drop. Never speed up the voice.
- **Loudness:** keep the skill's default (-14 LUFS).

Formats that suit this pitch (all checked in the skill's SKILL.md): **43** screen recording, **24** stat tiles,
**27** pipeline, **4a** logo card / **34** magnetic dock, **2** lower line, **35** masked title, **47** subtitles.

---

## 6. Your part during the edit

The skill stops twice for you. Plan **15–30 minutes** of your own time.

- **Checkpoint A, the cut:** it writes `PAPER-CUT.md` (what it kept and cut). Answer in plain words,
  e.g. "keep the earlier take of the intro".
- **Checkpoint B, the plan:** `storyboard.json` + its snapshot sheet. Check that text is readable, that
  nothing covers the pill or field being shown, and that every number matches its screenshot. Ideally a
  teammate checks the numbers too. Approve **before** it renders.
- After the render: watch it once with sound and once without. Put all fixes in **one list**.

Render time: about **10 minutes for 2:30 at 1080p** (the skill's figure: about 4 s per second of film on an M2 Mac).

---

## 7. Checklist and timeline (from now)

**The overnight-run decision (team):** the script's optional sentence A and the morning clip need real portals
on a dashboard. Either (a) start a run now on the hand-in commit with portals known to deliver documents
(e.g. Cyprus, E-ZAK MUNI, Berlin, Charité; attach ≈ 1–6 cents each, reads free), or (b) skip it and use
sentence B with the dashboard from the demo recording. Both are honest; (b) costs nothing.

**Night (now → 07:30)**
- [ ] Skill installed, setup check + tests pass
- [ ] 30-second test clip edited and rendered
- [ ] Script read aloud once with a timer (under 2:20)
- [ ] Logo and tool logos in `img/`, README cost section screenshotted
- [ ] Live demo recorded as `raw/pitch.mp4`
- [ ] Overnight run: decided (a) or (b)

**Saturday 07:30–09:00** (team plan v3: 07:30 capture evidence, 08:30–09:00 lock the release SHA and finish the cut)
- [ ] 07:30 `demo/01_dashboard_morning.mp4` + screenshots (only with option a)
- [ ] Team, links, upload details, music filled into `BRIEF.md`
- [ ] 07:45 edit started with `BRIEF.md` as the first message
- [ ] Checkpoint A answered, Checkpoint B approved
- [ ] 08:45 render checked with and without sound
- [ ] Under 2:30, under the file size limit
- [ ] Video final by 09:00; uploaded before the 10:30 team hand-in (organizer cutoff 11:00)
