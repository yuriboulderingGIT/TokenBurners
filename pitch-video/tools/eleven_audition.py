"""Make one audition clip per built-in ElevenLabs voice with the pitch's first line.

The key is read from pitch-video/.env and never printed. Stops at the first permission error.
Usage: python eleven_audition.py OUT_DIR
"""

import json
import sys
import urllib.request
from pathlib import Path

from _paths import PV  # noqa: E402

ENV = PV / ".env"
KEY = next(line.split("=", 1)[1].strip() for line in ENV.read_text(encoding="utf-8").splitlines()
           if line.strip().startswith("ELEVENLABS_API_KEY="))
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

TEXT = ("Public tenders are spread across countless portals. Every one of them looks different, "
        "and any of them can change overnight, without warning.")
VOICES = {  # ElevenLabs built-in voices (stable public IDs)
    "1-George-british-male-warm": "JBFqnCBsd6RMkjVDRZzb",
    "2-Daniel-british-male-news": "onwK4e9ZLuTAKqWW03F9",
    "3-Brian-american-male-deep": "nPczCjzI2devNBz1zQrb",
    "4-Alice-british-female-confident": "Xb7hH8MSUJpSbSDYk0k2",
    "5-Lily-british-female-warm": "pFZP5JQG7iQjIQuC4Bku",
    "6-Sarah-american-female-soft": "EXAVITQu4vr4xnSDxMaL",
}

for name, voice_id in VOICES.items():
    body = json.dumps({"text": TEXT, "model_id": "eleven_multilingual_v2"}).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        data=body, headers={"xi-api-key": KEY, "content-type": "application/json", "accept": "audio/mpeg"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            audio = res.read()
            cost = res.headers.get("character-cost") or res.headers.get("x-character-count")
    except urllib.error.HTTPError as err:
        print(name, "ERROR", err.code, err.read().decode("utf-8", "replace")[:300])
        if err.code in (401, 402, 403):
            break
        continue
    (OUT / f"{name}.mp3").write_bytes(audio)
    print(f"{name}: {len(audio) // 1024} KB, credits {cost}", flush=True)
