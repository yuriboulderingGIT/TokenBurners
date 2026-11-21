"""Show the ElevenLabs plan, credits and voices (free calls). The key is read from pitch-video/.env and never printed."""

import json
import urllib.request
from pathlib import Path

from _paths import PV  # noqa: E402

ENV = PV / ".env"
KEY = next(line.split("=", 1)[1].strip() for line in ENV.read_text(encoding="utf-8").splitlines()
           if line.strip().startswith("ELEVENLABS_API_KEY="))


def get(path: str):
    req = urllib.request.Request("https://api.elevenlabs.io" + path, headers={"xi-api-key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        return {"error": err.code, "body": err.read().decode("utf-8", "replace")[:300]}


sub = get("/v1/user/subscription")
if "error" in sub:
    print("subscription:", sub)
else:
    keys = ("tier", "status", "character_count", "character_limit", "next_character_count_reset_unix",
            "can_use_instant_voice_cloning", "can_use_professional_voice_cloning")
    print({k: sub.get(k) for k in keys})

voices = get("/v1/voices")
if "error" in voices:
    print("voices:", voices)
else:
    out = []
    for v in voices.get("voices", []):
        labels = v.get("labels") or {}
        out.append({
            "id": v["voice_id"], "name": v["name"], "category": v.get("category"),
            "gender": labels.get("gender"), "accent": labels.get("accent"), "age": labels.get("age"),
            "use": labels.get("use_case") or labels.get("use case"), "desc": labels.get("descriptive") or labels.get("description"),
            "preview": v.get("preview_url"),
        })
    Path(__file__).with_name("voices.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for v in out:
        print(f"{v['name']:<28} {v['category'] or '':<10} {v['gender'] or '':<7} {v['accent'] or '':<14} {v['age'] or '':<12} {v['use'] or '':<22} {v['desc'] or ''}")
