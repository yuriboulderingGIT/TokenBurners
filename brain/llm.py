"""The ONLY way to call a model. OWNER: B1.

Logs tokens and dollars per portal (llm_calls table), caches identical prompts on disk
(free reruns while developing), and refuses calls past the budget cap.

Brain code receives a ScopedLLM (bound to one portal and run):
    data = await llm.json(prompt, system="...")            # cheap model
    data = await llm.json(prompt, system="...", strong=True)  # strong model (repair)
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import LLM_BUDGET_USD, LLM_CACHE, LLM_CACHE_DIR, OPENAI_MODEL, OPENAI_MODEL_STRONG
from app.db import DB

# USD per 1M tokens (input, output). TODO(B1): check these on OpenAI's pricing page.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
}
DEFAULT_PRICE = (2.0, 10.0)  # unknown model: assume expensive, so the budget errs on the safe side


class BudgetExceeded(Exception):
    """The model budget is used up. Keep serving the last good tenders and say so in reason."""


class LLMUnavailable(Exception):
    """Quota error, missing key or API outage."""


class LLM:
    def __init__(
        self,
        db: DB,
        budget_usd: float = LLM_BUDGET_USD,
        cheap_model: str = OPENAI_MODEL,
        strong_model: str = OPENAI_MODEL_STRONG,
        cache_dir: Path | None = LLM_CACHE_DIR if LLM_CACHE else None,
    ) -> None:
        self.db = db
        self.budget_usd = budget_usd
        self.cheap_model = cheap_model
        self.strong_model = strong_model
        self.cache_dir = cache_dir
        self._client = None

    def scoped(self, portal_id: str, run_id: str | None, purpose: str) -> "ScopedLLM":
        return ScopedLLM(self, portal_id, run_id, purpose)

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI  # imported lazily so tests don't need a key

            try:
                self._client = AsyncOpenAI()  # reads OPENAI_API_KEY
            except Exception as e:
                raise LLMUnavailable(f"OpenAI client could not start: {e}") from e
        return self._client

    async def call_json(
        self, prompt: str, system: str, model: str, portal_id: str, run_id: str | None, purpose: str
    ) -> tuple[dict[str, Any], float]:
        """Returns (parsed JSON answer, cost in USD)."""
        key = hashlib.sha256(f"{model}\n{system}\n{prompt}".encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8")), 0.0

        if self.db.llm_total_cost() >= self.budget_usd:
            raise BudgetExceeded(f"model budget of ${self.budget_usd:.2f} is used up")

        try:
            response = await self._get_client().responses.create(
                model=model,
                instructions=system + " Always answer with valid JSON only.",
                input=prompt,
                text={"format": {"type": "json_object"}},
            )
        except LLMUnavailable:
            raise
        except Exception as e:  # TODO(B1): tell quota errors apart from temporary ones and retry those
            raise LLMUnavailable(f"model call failed: {e}") from e

        usage = response.usage
        tokens_in = getattr(usage, "input_tokens", 0) or 0
        tokens_out = getattr(usage, "output_tokens", 0) or 0
        price_in, price_out = PRICES.get(model, DEFAULT_PRICE)
        cost = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
        self.db.llm_log(portal_id, run_id, model, tokens_in, tokens_out, cost, purpose)

        data = json.loads(response.output_text)
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data), encoding="utf-8")
        return data, cost


class ScopedLLM:
    """An LLM bound to one portal + run, so every call is logged against it."""

    def __init__(self, llm: LLM, portal_id: str, run_id: str | None, purpose: str) -> None:
        self.llm = llm
        self.portal_id = portal_id
        self.run_id = run_id
        self.purpose = purpose
        self.total_cost = 0.0

    @property
    def model_name(self) -> str:
        return self.llm.cheap_model

    async def json(
        self, prompt: str, system: str = "You are a precise web scraping assistant.",
        strong: bool = False, purpose: str | None = None,
    ) -> dict[str, Any]:
        model = self.llm.strong_model if strong else self.llm.cheap_model
        data, cost = await self.llm.call_json(
            prompt, system, model, self.portal_id, self.run_id, purpose or self.purpose
        )
        self.total_cost += cost
        return data
