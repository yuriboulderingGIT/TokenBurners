"""Run the spending guard against a local SQLite database and a fixture model."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.db import DB
from brain.llm import BudgetExceeded, LLM, LLMUnavailable


def scoped(db, create, *, budget=0.01, cache=None, model="gpt-5-mini"):
    llm = LLM(db, budget_usd=budget, cheap_model=model, cache_dir=cache)
    llm._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return llm.scoped("portal", "run", "attach")


def response(output_tokens, text='{"ok":true}'):
    return SimpleNamespace(
        output_text=text,
        usage=SimpleNamespace(input_tokens=32, output_tokens=output_tokens),
    )


def test_concurrent_workers_share_a_single_budget(tmp_path):
    async def exercise():
        path = tmp_path / "watchtower.db"
        db = DB(path)
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = []

        async def create(**request):
            calls.append(request)
            started.set()
            await finish.wait()
            return response(4_000)

        clients = [scoped(DB(path), create) for _ in range(4)]
        first = asyncio.create_task(clients[0].json("first"))
        await asyncio.wait_for(started.wait(), timeout=2)
        others = await asyncio.gather(
            *(client.json("another portal read") for client in clients[1:]), return_exceptions=True
        )
        assert all(isinstance(error, BudgetExceeded) for error in others)
        assert db.llm_total_cost() == 0 and len(calls) == 1
        finish.set()
        assert await first == {"ok": True}
        assert db.llm_total_cost() == pytest.approx(0.008008)
        assert clients[0].total_cost == pytest.approx(db.llm_total_cost())
        assert not db.all("SELECT * FROM llm_reservations")
        with pytest.raises(BudgetExceeded):
            await clients[1].json("one more")
        assert len(calls) == 1

    asyncio.run(exercise())


def test_rejected_requests_release_budget_and_bad_json_is_billed(tmp_path):
    class RateLimit(Exception):
        status_code = 429

    replies = iter([RateLimit(), response(100, "bad json"), response(100)])

    async def create(**request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def exercise():
        db = DB(tmp_path / "watchtower.db")
        client = scoped(db, create, cache=tmp_path / "cache")
        with pytest.raises(LLMUnavailable, match="HTTP 429"):
            await client.json("same prompt")
        assert db.llm_total_cost() == 0 and not db.all("SELECT * FROM llm_reservations")
        with pytest.raises(json.JSONDecodeError):
            await client.json("same prompt")
        assert client.total_cost == pytest.approx(0.000208)
        assert await client.json("same prompt") == {"ok": True}
        assert db.llm_total_cost() == pytest.approx(2 * 0.000208)
        assert client.total_cost == pytest.approx(db.llm_total_cost())
        client.llm.budget_usd = 0
        assert await client.json("same prompt") == {"ok": True}
        assert len(db.all("SELECT * FROM llm_calls")) == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["cancelled", "missing usage", "disconnected"])
def test_uncertain_usage_preserves_budget_across_restart(tmp_path, failure):
    async def create(**request):
        if failure == "cancelled":
            raise asyncio.CancelledError
        if failure == "disconnected":
            raise TimeoutError
        return SimpleNamespace(output_text="{}", usage=None)

    async def exercise():
        path = tmp_path / "watchtower.db"
        db = DB(path)
        client = scoped(db, create)
        error = asyncio.CancelledError if failure == "cancelled" else LLMUnavailable
        with pytest.raises(error):
            await client.json("first")
        assert db.llm_total_cost() == 0  # no confirmed usage yet
        assert len(db.all("SELECT * FROM llm_reservations")) == 1
        with pytest.raises(BudgetExceeded):
            await scoped(DB(path), create).json("again")

    asyncio.run(exercise())


def test_unpriced_model_is_rejected_before_dispatch(tmp_path):
    db = DB(tmp_path / "watchtower.db")
    client = scoped(db, lambda **kwargs: pytest.fail("dispatched"), model="unpriced")
    with pytest.raises(LLMUnavailable, match="no budget pricing"):
        asyncio.run(client.json("test"))
    assert not db.all("SELECT * FROM llm_reservations")
