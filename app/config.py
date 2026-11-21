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
# A portal that never attached has nothing to re-read, so only a manual refresh retries it.
FAILED_RETRY_MINUTES = float(os.getenv("FAILED_RETRY_MINUTES", "180"))
# A portal whose first read hit a temporary portal error (network, timeout, HTTP 5xx) stays
# integrating and is tried again this soon, up to FIRST_READ_TRIES times before it is failed.
INTEGRATING_RETRY_MINUTES = float(os.getenv("INTEGRATING_RETRY_MINUTES", "5"))
FIRST_READ_TRIES = int(os.getenv("FIRST_READ_TRIES", "3"))
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "16"))
# A manual refresh of a failed portal may start a model repair at most this often. Clients may
# call refresh every few minutes; the dashboard's Repair and Relearn buttons are not limited.
REFRESH_REPAIR_MINUTES = float(os.getenv("REFRESH_REPAIR_MINUTES", "60"))

# Fetcher: politeness rule from the brief is at most 1 request per second per portal.
# 1.1 s leaves a margin, so network jitter can't make two requests arrive < 1 s apart.
MIN_REQUEST_INTERVAL = max(1.0, float(os.getenv("MIN_REQUEST_INTERVAL", "1.1")))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
USER_AGENT = os.getenv("USER_AGENT", "Watchtower/0.1 (public tender monitor)")
# Off by default: the portals to monitor may sit on a private network. Set to 1 for a public
# deployment so portal addresses, redirects, feeds and files must resolve to public addresses.
PUBLIC_DESTINATIONS_ONLY = os.getenv("PUBLIC_DESTINATIONS_ONLY", "0") == "1"
# With PUBLIC_DESTINATIONS_ONLY=1: the one trusted private host:port allowed, e.g. a local mock.
MOCK_PORTAL_ADDRESS = os.getenv("MOCK_PORTAL_ADDRESS", "").strip()

# Documents
MAX_DOC_BYTES = 250 * 1024 * 1024
# Known files checked again per read (ETag/Last-Modified or a fresh download), stalest first.
DOC_REVALIDATIONS_PER_READ = int(os.getenv("DOC_REVALIDATIONS_PER_READ", "30"))

# Model (all calls go through brain/llm.py)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_MODEL_STRONG = os.getenv("OPENAI_MODEL_STRONG", "gpt-5")
LLM_BUDGET_USD = float(os.getenv("LLM_BUDGET_USD", "5"))
# Disk cache of model answers, for free reruns while developing (.env.example turns it on).
# Off by default: answers are cached before anyone knows they were good, so in production a
# failed attach would replay the same bad answers on every retry.
LLM_CACHE = os.getenv("LLM_CACHE", "0") == "1"
