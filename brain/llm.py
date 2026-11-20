"""The ONLY way to call a model. OWNER: B1.

Logs tokens and dollars per portal (llm_calls table), caches identical prompts on disk
(free reruns while developing), and refuses calls past the budget cap.

Brain code receives a ScopedLLM (bound to one portal and run):
    data = await llm.json(prompt, system="...")            # cheap model
    data = await llm.json(prompt, system="...", strong=True)  # strong model (repair)
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.config import LLM_BUDGET_USD, LLM_CACHE, LLM_CACHE_DIR, OPENAI_MODEL, OPENAI_MODEL_STRONG
from app.db import DB

# Standard-tier USD per million text tokens for the configured models.
# https://developers.openai.com/api/docs/models/gpt-5-mini
# https://developers.openai.com/api/docs/models/gpt-5
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
}
MAX_OUTPUT_TOKENS = 4_096
CONTEXT_WINDOW = 400_000


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
                self._client = AsyncOpenAI(max_retries=0)  # one paid request per reservation
            except Exception as e:
                raise LLMUnavailable("OpenAI client could not start. Check OPENAI_API_KEY.") from e
        return self._client

    async def call_json(
        self, prompt: str, system: str, model: str, portal_id: str, run_id: str | None, purpose: str,
        *, on_cost: Callable[[float], None] | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Returns (parsed JSON answer, cost in USD)."""
        key = hashlib.sha256(f"{model}\n{system}\n{prompt}".encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8")), 0.0

        prices = PRICES.get(model.removesuffix("-2025-08-07"))
        if prices is None:
            raise LLMUnavailable(f"no budget pricing configured for model {model}")
        price_in, price_out = prices
        instructions = system + " Always answer with valid JSON only."
        model_input = "Answer in JSON.\n" + prompt
        # These text-only models tokenize bytes. Include headroom for the fixed Responses envelope.
        max_input = min(CONTEXT_WINDOW, len(instructions.encode()) + len(model_input.encode()) + 1_024)
        max_cost = (max_input * price_in + MAX_OUTPUT_TOKENS * price_out) / 1e6
        reservation = self.db.llm_try_reserve(portal_id, run_id, max_cost, self.budget_usd)
        if reservation is None:
            raise BudgetExceeded(f"insufficient unreserved budget for this call (cap ${self.budget_usd:.2f})")

        try:
            response = await self._get_client().responses.create(
                model=model,
                instructions=instructions,
                input=model_input,
                text={"format": {"type": "json_object"}},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                service_tier="default",
            )
        except LLMUnavailable:
            self.db.llm_release(reservation)  # client initialization failed before dispatch
            raise
        except Exception as e:
            status = getattr(e, "status_code", None)
            quota_error = getattr(e, "code", None) == "insufficient_quota"
            if quota_error or status in {400, 401, 403, 404, 413, 422, 429} or isinstance(
                e.__cause__, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
            ):
                self.db.llm_release(reservation)
            else:
                # A disconnected client cannot tell whether generation was billed.
                raise LLMUnavailable("model usage unconfirmed after request failure. Budget reservation retained.") from e
            if quota_error:
                raise LLMUnavailable("OpenAI quota exhausted") from e
            reason = f"HTTP {status}" if status else type(e).__name__
            raise LLMUnavailable(f"model call failed ({reason})") from e

        usage = response.usage
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
        if tokens_in is None or tokens_out is None:
            raise LLMUnavailable("model returned no token usage. Budget reservation retained.")
        cached_tokens = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        input_cost = ((tokens_in - cached_tokens) + cached_tokens / 10) * price_in / 1e6
        cost = input_cost + tokens_out * price_out / 1e6
        self.db.llm_log(portal_id, run_id, model, tokens_in, tokens_out, cost, purpose, reservation=reservation)
        if on_cost is not None:
            on_cost(cost)

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

    @property
    def strong_model_name(self) -> str:
        return self.llm.strong_model

    def _add_cost(self, cost: float) -> None:
        self.total_cost += cost

    async def json(
        self, prompt: str, system: str = "You are a precise web scraping assistant.",
        strong: bool = False, purpose: str | None = None,
    ) -> dict[str, Any]:
        model = self.llm.strong_model if strong else self.llm.cheap_model
        data, _ = await self.llm.call_json(
            prompt, system, model, self.portal_id, self.run_id, purpose or self.purpose, on_cost=self._add_cost,
        )
        return data
