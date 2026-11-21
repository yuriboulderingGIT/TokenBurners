"""Make the voice-over from narration.json with ElevenLabs, with word timings for the edit.

Usage:  python voice.py VOICE_ID            generate missing/changed sections, then assemble
        python voice.py VOICE_ID --assemble only assemble from the cached sections (no credits)

Each section is cached in ../voice/sections/<VOICE_ID>/<id>.mp3 + .json (keyed by its text), so changing one
line only re-buys that line. The key is read from ../.env and never printed.
Writes assets/voice/voice.wav, words.json (every word with start/end in film time) and sections.json.
"""

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PV = HERE.parent
VOICE_ID = sys.argv[1]
ASSEMBLE_ONLY = "--assemble" in sys.argv
MODEL = "eleven_multilingual_v2"
FFMPEG = shutil.which("ffmpeg") or str(next(Path.home().glob(
    "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin/ffmpeg.exe")))
CACHE = PV / "voice" / "sections" / VOICE_ID
CACHE.mkdir(parents=True, exist_ok=True)
sections = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))["sections"]


def key() -> str:
    for line in (PV / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ELEVENLABS_API_KEY missing in pitch-video/.env")


def generate(i: int, sec: dict) -> dict:
    digest = hashlib.sha1(f"{MODEL}|{sec['text']}".encode()).hexdigest()[:10]
    meta_path, mp3_path = CACHE / f"{sec['id']}.json", CACHE / f"{sec['id']}.mp3"
    if meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")).get("digest") == digest:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    if ASSEMBLE_ONLY:
        raise SystemExit(f"section {sec['id']} is not generated yet")
    body = {"text": sec["text"], "model_id": MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True}}
    if i > 0:
        body["previous_text"] = sections[i - 1]["text"]
    if i + 1 < len(sections):
        body["next_text"] = sections[i + 1]["text"]
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), headers={"xi-api-key": key(), "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            data = json.loads(res.read())
            cost = res.headers.get("character-cost")
    except urllib.error.HTTPError as err:
        raise SystemExit(f"{sec['id']}: HTTP {err.code} {err.read().decode('utf-8', 'replace')[:300]}")
    mp3_path.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("normalized_alignment") or data["alignment"]
    meta = {"digest": digest, "credits": cost, "chars": al["characters"],
            "starts": al["character_start_times_seconds"], "ends": al["character_end_times_seconds"]}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    print(f"generated {sec['id']}: credits {cost}", flush=True)
    return meta


def words_of(meta: dict) -> list[dict]:
    out, cur = [], None
    for ch, s, e in zip(meta["chars"], meta["starts"], meta["ends"]):
        if ch.isspace():
            if cur:
                out.append(cur)
            cur = None
            continue
        if cur is None:
            cur = {"text": ch, "start": s, "end": e}
        else:
            cur["text"] += ch
            cur["end"] = e
    if cur:
        out.append(cur)
    return out


def duration(path: Path) -> float:
    probe = subprocess.run([str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix)), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True)
    return float(probe.stdout.strip())


metas = [generate(i, sec) for i, sec in enumerate(sections)]

# Assemble: gap of silence before each section, then the section (decoded to 48 kHz mono WAV).
work = PV / "voice" / "work"
work.mkdir(parents=True, exist_ok=True)
t, words, spans, parts = 0.0, [], [], []
for sec, meta in zip(sections, metas):
    wav = work / f"{sec['id']}.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(CACHE / f"{sec['id']}.mp3"), "-ar", "48000", "-ac", "1", str(wav)],
                   check=True)
    gap = work / f"gap-{sec['id']}.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{sec['gap']:.3f}",
                    str(gap)], check=True)
    parts += [gap, wav]
    t += sec["gap"]
    length = duration(wav)
    for w in words_of(meta):
        words.append({"id": f"w{len(words)}", "text": w["text"], "start": round(t + w["start"], 3),
                      "end": round(t + w["end"], 3), "section": sec["id"]})
    spans.append({"id": sec["id"], "start": round(t, 3), "end": round(t + length, 3)})
    t += length

listing = work / "concat.txt"
listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
out = HERE / "assets" / "voice" / "voice.wav"
out.parent.mkdir(parents=True, exist_ok=True)
raw = work / "voice-raw.wav"
subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(raw)], check=True)
# Level the voice to about -16 LUFS with a -1.5 dBTP ceiling.
subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", str(out)],
               check=True)
(HERE / "words.json").write_text(json.dumps(words, indent=0), encoding="utf-8")
(HERE / "sections.json").write_text(json.dumps({"voice": VOICE_ID, "total": round(t, 3), "sections": spans}, indent=1),
                                     encoding="utf-8")
print(f"voice {t:.1f} s, {len(words)} words -> {out}")
