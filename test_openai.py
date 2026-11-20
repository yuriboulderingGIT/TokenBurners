"""Quick check that YOUR OpenAI key works. Run: python test_openai.py

Goes through brain/llm.py like the real system, so the call is logged with its cost.
"""

import asyncio

from app.db import db
from brain.llm import LLM


async def main() -> None:
    llm = LLM(db, cache_dir=None).scoped(portal_id="key-check", run_id=None, purpose="check")
    data = await llm.json('Give me a JSON object with keys "team" and "mood".')
    print("JSON reply:", data)
    print(f"Cost: ${llm.total_cost:.6f}")
    print("OK - OpenAI works.")


asyncio.run(main())
