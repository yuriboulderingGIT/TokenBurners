import asyncio

import httpx

from testset.run_all import markdown, observe


def test_runner_polls_until_settled_and_counts_files_and_spend():
    states = iter(["integrating", "healing", "serving"])

    def reply(req):
        if req.method == "POST":
            return httpx.Response(201, json={"id": "abc"})
        if req.url.path == "/portals/abc":
            return httpx.Response(200, json={"state": next(states), "reason": "read | ok"})
        if req.url.path.endswith("/tenders"):
            return httpx.Response(200, json=[{"documents": [1, 2]}, {"documents": []}])
        assert req.url.path == "/ui/portals/abc"
        return httpx.Response(200, text='<b data-model-cost-usd="0.0051234">$0.005</b>')

    async def run():
        async with httpx.AsyncClient(base_url="http://wt", transport=httpx.MockTransport(reply)) as c:
            return await observe(c, {"name": "fixture", "url": "http://example/list"}, 5, 0)

    row = asyncio.run(run())
    assert (row.state, row.tenders, row.documents, row.cost) == ("serving", 2, 2, 0.0051234)
    assert r"read \| ok | 2 | 2 | 0.005123" in markdown([row])


def test_runner_reports_lost_observation_without_inventing_portal_state_or_cost():
    async def run():
        async with httpx.AsyncClient(base_url="http://wt", transport=httpx.MockTransport(
            lambda req: httpx.Response(503)
        )) as c:
            return await observe(c, {"name": "portal", "url": "http://example/list"}, 0)
    row = asyncio.run(run())
    assert row.incomplete and row.state == "unobserved"
    assert "503" in row.reason and row.cost is None


def test_refresh_observes_new_state_after_preexisting_failure():
    snapshots = iter(["failed", "serving"])

    def reply(req):
        path = req.url.path
        if path == "/portals":
            return httpx.Response(201, json={"id": "abc"})
        if path.endswith("/refresh"):
            return httpx.Response(202, json={"runId": "new"})
        if path.endswith("/runs"):
            return httpx.Response(200, json=[{"id": "new", "result": "ok"}])
        if path.endswith("/tenders"):
            return httpx.Response(200, json=[])
        if path.startswith("/ui"):
            return httpx.Response(200, text='<b data-model-cost-usd="0">$0</b>')
        return httpx.Response(200, json={"state": next(snapshots), "reason": ""})

    async def run():
        async with httpx.AsyncClient(base_url="http://wt", transport=httpx.MockTransport(reply)) as c:
            return await observe(c, {"name": "p", "url": "http://example/list"}, 5, 0, refresh=True)

    assert asyncio.run(run()).state == "serving"
