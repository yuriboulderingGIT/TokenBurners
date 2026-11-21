# Watchtower pitch: script and shot list

*Updated Saturday 21 Nov, 01:30, against repo `main` at `66444f9`.*

One video, **max 2:30**, aim for 2:15–2:20. You speak over a live demo you record yourself.
The cost numbers are now measured and written in the repo README ("Measured model cost"), so they no
longer wait for the overnight run. Only the *(optional)* overnight sentence still depends on the morning.

Read it out loud once with a timer before recording. If you're over 2:20, drop sentences marked *(optional)*.

---

## The script (≈ 320 words, about 2:10–2:15 spoken)

### 1 · Hook (0:00–0:10) · on screen: the Watchtower dashboard
> Public tenders are spread across countless portals. Every one of them looks different, and any of them can change overnight, without warning.

### 2 · Problem (0:10–0:35) · on screen: dashboard, or screenshots of three real portals
> For a company like Patterno, that means a hand-written scraper for every portal. And scrapers break silently. When a portal changes its layout, the data just stops being right, and nobody notices until a client misses a deadline.

### 3 · Solution (0:35–0:55) · on screen: dashboard, empty "add portal" form
> So we built Watchtower. You give it one thing: the address of a tender portal it has never seen. It learns how to read that portal once, reads it for free from then on, notices when something breaks, and repairs itself. Nobody has to help it.

### 4 · Demo (0:55–1:45) · live, see "Recording the demo" below
> Here I add a portal. Watchtower explores it, and the model writes a recipe: which rows are tenders, where the title, the buyer and the deadline are, and where the documents are. The recipe is tested on three tenders, and the portal turns green: serving.

> Every tender comes with its documents. ZIP files are unpacked, and deadlines get the portal's time zone.

> Now I change the portal's layout on purpose. The next read fails its health check, so the portal goes to healing. The stronger model rewrites the recipe, version two passes, and we are serving again, with the same tender IDs.

> And when a portal puts up a captcha, Watchtower doesn't pretend. It says failed, and it tells you why.

### 5 · How it works (1:45–2:00) · on screen: dashboard (a diagram goes on top)
> How it works, in four steps. First, an OpenAI model learns a recipe, once. Second, plain Python code reads with it, with no model calls. Third, a health check after every read. Fourth, when a portal changes, the model repairs the recipe.

### 6 · Impact and close (2:00–2:20) · on screen: the morning dashboard
> *(optional, pick one or drop it)* **A:** Overnight, it kept **[FILL: number of real portals serving *with documents*, e.g. "five"]** real portals up to date, every fifteen minutes.
> **B (no overnight run needed):** On real European portals, it tells you exactly which ones deliver, and for the others, exactly why not.

> Learning a portal costs about one to two cents, a repair about three cents, and a regular read costs nothing. It's one Docker container, polite to every portal, and honest about what it delivers.

> We are TokenBurners, and this is Watchtower.

**Where the numbers come from** (README, "Measured model cost", 20 Nov): attach on the mock $0.0055, attach on Cyprus
$0.019, one repair $0.028, a regular read $0. Screenshot that README section, or the portal page's "Model cost", into
`img/screenshots/`. Use sentence **A** only if the morning dashboard shows that many real portals **serving with documents**:
since 21 Nov a portal without any documents shows *failed*, so "serving" now means real delivery.

---

## Shot list (for BRIEF.md and the edit)

Format numbers checked against the downloaded skill's SKILL.md.

| When you say… | What the edit shows | Skill format |
|---|---|---|
| "countless portals" | screenshots of 3 real portal list pages, side by side | 24 tiles or simple cut |
| "So we built Watchtower" | title card: **Watchtower** | 35 Masked title |
| "Here I add a portal" | zoom on the add-portal field, typing the URL | 43 Screen recording |
| "the model writes a recipe" | zoom on "v1 learned" on the portal page | 43 |
| "the portal turns green: serving" | zoom on the green **serving** pill | 43 |
| "Every tender comes with its documents" | zoom on the tender list / document names | 43 |
| "change the portal's layout on purpose" | the mock portal page switching to the new layout | 43 |
| "goes to healing" | zoom on the amber **healing** pill | 43 |
| "version two passes" | zoom on recipe **v1 → v2** ("v2 repaired") | 43 |
| "puts up a captcha" | zoom on **failed** + the reason "blocked: captcha required" | 43 |
| "in four steps. First…" | pipeline: Learn → Read → Check → Repair | 27 Pipeline |
| "an OpenAI model" | OpenAI logo card | 4a Logo card |
| "plain Python code" / "one Docker container" | Python / Docker logos | 34 Magnetic dock |
| "Overnight, it kept…" / "On real European portals…" | demo/01_dashboard_morning.mp4 | cut to clip |
| "costs about … a repair about … nothing" | three stat tiles: Attach ≈ $0.01–0.02 · Repair ≈ $0.03 · Read $0 | 24 Stat tiles |
| "honest about what it delivers" | lower line: "Honest states: serving · healing · failed" | 2 Lower line |
| "this is Watchtower" | end card: Watchtower · TokenBurners · team · repo link | end card |

---

## Recording the demo

Not recorded yet (no `raw/` folder exists). Record it as soon as possible; the mock demo does not depend on the
overnight run. The flow was checked on `66444f9` with the offline proof (attach → layout change → repair → captcha →
failed).

**Costs credits:** each full take makes real model calls, about **3–4 cents** (attach ≈ $0.006, repair ≈ $0.03).
A rehearsal plus two takes ≈ 10 cents. Use your own key in `.env`, never the jury's hand-in key.

**Setup, once**
1. Stop any Watchtower already running on port 8080 (a test server may still be running there).
2. Two terminals in the repo (`TokenBurners`). Start the mock portal and Watchtower with a fresh data folder under
   `data/` (git ignores it, so the demo database can't be committed). The commands are below this list.
3. Reset the mock: open http://localhost:8090/_switch?reset=1
4. Browser: zoom **150 %**, hide the bookmarks bar, close other tabs. Nothing personal, no `.env`, no terminal with a key on screen.
5. Tab 1: http://localhost:8080 (dashboard). Tab 2: http://localhost:8090/tenders (the mock portal).
6. Recorder: **OBS Studio**, display capture + microphone, **the screen's full resolution (4K if you have it)**, 30 fps.
   The skill notes that 1080p goes soft past ~112 % zoom and 4K holds 200 %; the edit zooms into small pills.
   One second of silence at the start and end.
7. Rehearse the whole demo once without recording, then reset (delete `data\demo`, reset the mock).

Terminal 1, the mock portal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn mock_portal.server:app --port 8090
```

Terminal 2, Watchtower:

```powershell
$env:DATA_DIR = "./data/demo"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8080
```

**The demo, step by step** (stay silent while waiting; the edit cuts pauses)
1. Dashboard → paste `http://localhost:8090/tenders` into the field at the top → **Add portal**.
   Wait for **serving** (about a minute). The portal page shows the recipe as "v1 learned".
2. Open the portal page: show tenders and documents (MOCK-003 has the unpacked ZIP files).
3. Tab 2: open http://localhost:8090/_switch?layout=v2, then show http://localhost:8090/tenders (new layout).
4. Portal page → **Read again now**. Wait: **healing** → **serving**, recipe **v2** ("v2 repaired").
5. Tab 2: http://localhost:8090/_switch?variant=captcha. Portal page → **Read again now** → **failed: blocked: captcha required**.
6. Reset: http://localhost:8090/_switch?reset=1

If a live step fails, keep recording, pause two seconds and do it again. The last take wins.

**Saturday 07:30:** record a 10–15 s clip of the dashboard with real portals as `demo/01_dashboard_morning.mp4`, plus
screenshots of any number you say out loud. No overnight run is confirmed yet: see the timeline in
`pitch-video-requirements.md` for the decision.
