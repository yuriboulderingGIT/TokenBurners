"""All settings come from environment variables (or a local .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "watchtower.db"
FILES_DIR = DATA_DIR / "files"
LLM_CACHE_DIR = DATA_DIR / "llm_cache"

# Worker
REFRESH_MINUTES = float(os.getenv("REFRESH_MINUTES", "15"))
# Failed portals are retried with a plain read (no repair, so no model cost) this often.
FAILED_RETRY_MINUTES = float(os.getenv("FAILED_RETRY_MINUTES", "180"))
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "16"))

# Fetcher: politeness rule from the brief is at most 1 request per second per portal.
# 1.1 s leaves a margin, so network jitter can't make two requests arrive < 1 s apart.
MIN_REQUEST_INTERVAL = max(1.0, float(os.getenv("MIN_REQUEST_INTERVAL", "1.1")))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
USER_AGENT = os.getenv("USER_AGENT", "Watchtower/0.1 (public tender monitor)")

# Documents
MAX_DOC_BYTES = 250 * 1024 * 1024

# Model (all calls go through brain/llm.py)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_MODEL_STRONG = os.getenv("OPENAI_MODEL_STRONG", "gpt-5")
LLM_BUDGET_USD = float(os.getenv("LLM_BUDGET_USD", "5"))
LLM_CACHE = os.getenv("LLM_CACHE", "1") == "1"
