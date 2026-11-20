"""Test settings. These run BEFORE the app is imported, so the app uses a throwaway database."""

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="watchtower-test-")
os.environ["REFRESH_MINUTES"] = "100000"  # no timed refreshes during tests
os.environ["LLM_BUDGET_USD"] = "0"  # tests must never spend money
os.environ.pop("OPENAI_API_KEY", None)
