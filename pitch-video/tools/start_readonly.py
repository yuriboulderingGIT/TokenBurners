"""Serve a COPY of an earlier run's data on port 8081 for screenshots: no API key, no automatic reads."""

import os
import shutil
import sys
from pathlib import Path

from _paths import PV, REPO

DEMO = REPO
SRC = REPO / "data" / sys.argv[1]
COPY = PV / "capture" / f"{sys.argv[1]}-copy"

if COPY.exists():
    shutil.rmtree(COPY)
COPY.mkdir()
shutil.copy2(SRC / "watchtower.db", COPY / "watchtower.db")

os.chdir(DEMO)
sys.path.insert(0, str(DEMO))
os.environ.update({
    "DATA_DIR": str(COPY),
    "OPENAI_API_KEY": "",
    "LLM_BUDGET_USD": "0",
    "REFRESH_MINUTES": "6000",
    "REFRESH_REPAIR_MINUTES": "6000",
})
import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=8081)
