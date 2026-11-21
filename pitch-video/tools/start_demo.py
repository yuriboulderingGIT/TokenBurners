"""Start the mock portal (8090) and Watchtower (8080) from the repo for filming.

The OpenAI key is read from the repo's .env into this process only; it is never printed.
Data goes to <repo>/data/video (git-ignored); spending is capped at $0.20 and automatic reads are off.
Usage: python start_demo.py mock|app
"""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from _paths import REPO

DEMO = REPO

os.chdir(DEMO)
sys.path.insert(0, str(DEMO))

if sys.argv[1] == "app":
    secrets = dotenv_values(REPO / ".env")
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_MODEL_STRONG"):
        if secrets.get(name):
            os.environ[name] = secrets[name]
    os.environ.update({
        "DATA_DIR": str(DEMO / "data" / "video"),
        "LLM_BUDGET_USD": "0.20",   # the user's cap for the video
        "LLM_CACHE": "0",
        "REFRESH_MINUTES": "600",   # no automatic reads while filming
        "REFRESH_REPAIR_MINUTES": "600",
        "PLAYWRIGHT_BROWSERS_PATH": str(REPO / "data" / "playwright"),
    })
    print("key loaded:", bool(os.environ.get("OPENAI_API_KEY")), flush=True)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8080)
else:
    import uvicorn
    uvicorn.run("mock_portal.server:app", host="127.0.0.1", port=8090)
