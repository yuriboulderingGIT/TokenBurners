"""Exercise fresh attach through the real SDK with a local Responses API transport."""

import asyncio
import json

import httpx
from openai import AsyncOpenAI

from app.db import DB
from app.fetcher import FakeFetcher
from brain.learn import learn_recipe
from brain.llm import LLM


def test_fresh_attach_sends_json_in_input_even_when_the_portal_does_not(tmp_path):
    url = "https://portal.example/tenders"
    prompts = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_object"
        model_input = payload["input"]
        if "json" not in model_input.lower():
            return httpx.Response(400, json={"error": {"message": "input must contain json"}})
        assert model_input.startswith("Answer in JSON.\n")
        prompt = model_input.removeprefix("Answer in JSON.\n")
        prompts.append(json.loads(prompt))
        if len(prompts) == 1:
            assert "json" not in prompt.lower()
            reply = {"list_url": url, "detail_url": None}
        else:
            reply = {
                "list": {"url": url, "item": "//article"},
                "fields": {"id": "./@data-id", "title": ".//h2"},
            }
        return httpx.Response(200, json={
            "object": "response", "id": "resp_fixture", "created_at": 1, "status": "completed",
            "model": payload["model"],
            "output": [{"type": "message", "role": "assistant", "status": "completed", "id": "msg_fixture",
                        "content": [{"type": "output_text", "text": json.dumps(reply), "annotations": []}]}],
            "usage": {"input_tokens": 50, "output_tokens": 30, "total_tokens": 80},
        })

    async def run():
        pages = FakeFetcher({url: "<article data-id='T-42'><h2>Library roof repairs</h2></article>"})
        db = DB(tmp_path / "budget.db")
        model = LLM(db, budget_usd=0.05, cheap_model="gpt-5-mini", cache_dir=None)
        async with AsyncOpenAI(
            api_key="fixture", base_url="https://model.example/v1/", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            model._client = client
            try:
                recipe = await learn_recipe(url, pages.for_portal("p"), model.scoped("p", "r", "attach"))
                assert recipe.list_.url == url and recipe.learned_by == "gpt-5-mini"
                assert len(prompts) == 2
                assert len(db.all("SELECT cost_usd FROM llm_calls")) == 2
                assert not db.all("SELECT id FROM llm_reservations")
            finally:
                await pages.aclose()

    asyncio.run(run())
