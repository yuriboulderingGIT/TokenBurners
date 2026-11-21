# Watchtower pitch video

The 2:08 hackathon pitch, made as code: an ElevenLabs voice-over timed word by word, real Watchtower
screenshots, and HyperFrames (HTML + GSAP rendered to MP4). Everything on screen is real: the demo was run
on the mock portal, the recipes are the ones the model wrote, and the real-portal shots come from a live run.

## What is where

| Path | What |
|---|---|
| `SCRIPT.md`, `BRIEF.md`, `pitch-video-requirements.md` | Script, shot list and brief (written before the edit) |
| `film/narration.json` | **The voice-over text**, one entry per section, with the pause before it |
| `film/build.py` | Writes the whole film (`index.html`, `compositions/*.html`) from the narration and word timings |
| `film/voice.py` | Makes the voice-over with ElevenLabs, with word timings (`words.json`, `sections.json`) |
| `film/prep_assets.py` | Turns raw captures (`capture/`, not in git) into `film/assets/img` |
| `film/qa.py` | Checks a render: streams, length, black frames, loudness, and pulls frames |
| `film/boxes.json` | Where the pill, field and rows sit on the captured pages (from `tools/measure.py`) |
| `tools/` | Capture tools: start the demo, drive it and take 4K stills, screenshot real portals |

Not in git: `.env` (the ElevenLabs key), `capture/` (raw 4K captures), `voice/` (voice cache), `output/`
(renders), `node_modules/`, `.venv/`.

## Rebuild and render

From `pitch-video/`, once: `npm install` (pins HyperFrames 0.9.22), FFmpeg on the PATH, and a Python with
`numpy` and `pillow`.

```powershell
cd film
python build.py                                # timings from words.json; prints the scene list
npx hyperframes check                          # lint, layout, contrast, text size
npx hyperframes snapshot --at 10,45,90         # stills to look at before rendering
npx hyperframes render -o ..\output\watchtower-pitch.mp4 --workers 2 --quality looks
python qa.py ..\output\watchtower-pitch.mp4
```

A render takes about 4 minutes on a laptop.

## Change the words or the voice

Edit `film/narration.json`, then `python voice.py <voice_id>`: only changed sections are bought again
(the full script is about 1,500 credits). `build.py` finds every cue by its spoken word (for example the
recipe card appears on "recipe"), so the edit follows the new timing; if a cue word is removed, the build
stops and names it. The first cut uses the voice `JBFqnCBsd6RMkjVDRZzb` (George).

## Shoot the demo again

`tools/start_demo.py mock` and `tools/start_demo.py app` start the mock portal and Watchtower (fresh data in
`data/video`, spending capped at $0.20, automatic reads off). Then `tools/capture.py <out> attach|repair|captcha`
runs each step and saves 4K stills; `tools/views.py` and `tools/measure.py` take the extra stills and element
boxes. One full run costs about 2 cents of OpenAI.

Sounds: HyperFrames' free library (Pixabay licence, see `film/assets/sfx/CREDITS.md`). Font: Mona Sans (SIL OFL).
