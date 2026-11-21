Hackathon pitch video. ONE video. HARD LIMIT 2:30 (aim 2:15–2:20). Use the youtube-edit skill.
Event: MAIN Summit Hackathon, Maastricht, Patterno track (Patterno allows up to 3:00; we keep 2:30).
Deadline: video finished Saturday 21 Nov 2026, 09:00 (release SHA locked 08:30–09:00); team hand-in 10:30;
organizer cutoff 11:00. Upload to: ___ (see MAIN submission page).
Format: 16:9, 1080p, 30 fps, max file size: ___ (see MAIN submission page).
Judging criteria (Patterno): Attach (tenders + documents from a portal it has never seen),
Notice (reported state matches what it delivers), Repair (correct again after a portal changes),
Dashboard (operator sees what delivers, what broke, what the system did), Approach (scales to many
portals; model cost per portal). The video must show the dashboard.

Product: Watchtower. Public tenders are spread over countless portals that all look different and
change without warning, so hand-written scrapers break silently. Watchtower gets only a portal's
address. An OpenAI model learns a "recipe" for reading it once; plain code then reads it every 15
minutes with no model calls; a health check runs after every read; when the portal changes, the model
repairs the recipe by itself. Different: cheap and predictable per portal, and honest. It says
serving, healing or failed with a reason, and never serves bad data. A portal whose tenders come
without any documents is reported as failed with the reason, not as serving. One Docker container.

How it works: 1. An OpenAI model (gpt-5-mini) explores the portal and writes a recipe, tested on 3
tenders, and is asked again if it missed visible documents. 2. Plain Python code reads tenders and
documents with the recipe, with no model calls. 3. A health check after every read. 4. When checks fail,
the stronger model (gpt-5) rewrites the recipe (max 3 tries); meanwhile the last correct tenders are kept.
Login or captcha → failed, with the reason.

Real numbers + source (repo README, "Measured model cost", 20 Nov; screenshots in img/screenshots/):
- Cost to learn a portal: about 1–2 cents (mock attach $0.0055; Cyprus $0.019; E-ZAK $0.008 with 482 documents)
- Cost per repair: about 3 cents ($0.028; the mock repaired in one call, same 5 IDs and 8 documents kept)
- Cost per regular read: $0 (no model calls)
- Portals serving with documents in the morning dashboard: ___ of ___ (only if the 07:30 screenshot shows it)
Only show a number if its screenshot is in img/screenshots/. Leave out any number without one.

Tech (say these, show logos): OpenAI (gpt-5-mini, gpt-5), Python, FastAPI, Playwright, SQLite, Docker.
Do NOT mention ElevenLabs or Streamlit; they are not part of the product.
Team: TokenBurners: ___ (Platform), ___ (Quality), ___ (Attach + repair), ___ (Data + JS portals)
End card links: ___ (repo link, only if the repo is public at hand-in)

Files:
- raw/pitch.mp4: the main recording. A SCREEN RECORDING of the live demo with my voice over it,
  not a talking head, recorded at the screen's full resolution with the browser at 150 %. I restart
  full sentences after a ~2 s pause; the last take wins. I stay silent while the app is working, so
  long waits can be cut. The full script is in SCRIPT.md.
- demo/01_dashboard_morning.mp4: 10–15 s of the dashboard with real portals (or leave it out and use
  the dashboard from raw/pitch.mp4 if there is no morning run)
- img/logo.___ (Watchtower wordmark), img/tools/ (OpenAI, Python, Docker, FastAPI, Playwright, SQLite),
  img/screenshots/ (every number I say + 3 real portal list pages), img/team.jpg (optional)
- music/___ (or: no music)

Shot list (full table in SCRIPT.md; format numbers match this skill's SKILL.md):
- "countless portals" → screenshots of 3 real portal pages
- "So we built Watchtower" → masked title (format 35): Watchtower
- "Here I add a portal" … "captcha" → format 43 zooms on raw/pitch.mp4: the Add portal field,
  "v1 learned", the green serving pill, the documents, the mock layout switch, the amber healing
  pill, "v2 repaired", the red failed pill with "blocked: captcha required"
- "in four steps. First…" → pipeline (format 27): Learn → Read → Check → Repair
- "an OpenAI model" → logo card (4a); "plain Python code", "one Docker container" → magnetic dock (34)
- "Overnight, it kept…" or "On real European portals…" → demo/01_dashboard_morning.mp4
- "costs about … a repair about … nothing" → three stat tiles (format 24): Attach ≈ $0.01–0.02 /
  Repair ≈ $0.03 / Read $0
- "honest about what it delivers" → lower line (format 2): Honest states: serving · healing · failed
- "this is Watchtower" → end card

Overrides to the skill's defaults:
- Replace the skill's example brand (sky blue #4FC3FF, gold #FFC93D, rose #FF4F7B, Manrope) with our
  house style (from our dashboard): colours #8b5cf6 (violet) / #6366f1 (indigo) / #18181b (ink),
  background #f7f6fc; state colours serving #10b981, healing #f59e0b, failed #ef4444.
  Font: Inter for text, EB Garamond for titles. Serious and clean, not playful.
- The main footage is a screen recording: keep the UI that is being talked about visible. Overlays must
  not cover the pill, field or text being zoomed on.
- Render 1080p, 30 fps (not the skill's 4K default; faster, and fine for the upload)
- End card allowed (product name, team, link)
- No subscribe bar (format 14), no description pointer, no chapter list
- Captions: yes (format 47, word by word)
- Music: ___ (yes with music/<track>.mp3, or no)
- If it runs over 2:30, suggest lines to drop (SCRIPT.md marks the optional ones); never speed up the voice

Stop at Checkpoint A and B for me. Use Sonnet for routine passes.
