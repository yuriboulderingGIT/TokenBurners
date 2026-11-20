# Developing Watchtower

The plan is **`Watchtower_Plan_v2.pdf`** (it replaces the task lists and timeline of
`Watchtower_Build_Plan.pdf`; the API contract, tender fields and recipe format of plan v1 still hold).
This page explains how to work in this repo.

## First-time setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env     # then paste YOUR OWN OpenAI key into .env
```

Mac: `python3.12 -m venv .venv`, `source .venv/bin/activate`, `cp .env.example .env`.

`.env` stays on your machine (it is gitignored). Everyone uses their own key while
developing. The judges get one separate hand-in key at submission.

## Running things

| What | Command | Open |
|---|---|---|
| Tests (run before every PR) | `pytest` | |
| API + dashboard | `uvicorn app.main:app --port 8080` | http://localhost:8080 |
| Mock portal | `uvicorn mock_portal.server:app --port 8090` | http://localhost:8090/tenders |
| Break the mock on purpose | | http://localhost:8090/_switch?layout=v2 (or `?variant=captcha`) |

Install Chromium with `python -m playwright install chromium` to discover public feeds on
JavaScript listings. On Windows, use a single worker and leave `--reload` off so
Playwright can launch a subprocess.

Add a portal (second terminal):

```powershell
curl.exe -X POST localhost:8080/portals -H "content-type: application/json" -d '{\"url\":\"http://localhost:8090/tenders\"}'
```

Or open http://localhost:8080/docs and use the "Try it out" buttons.

Start fresh: stop the server and delete the `data/` folder.

## Who owns what (plan v2)

| Role | Files |
|---|---|
| A1 Platform | `app/main.py`, `db.py`, `models.py`, `worker.py`, `fetcher.py`, `config.py`, `templates/`, `Dockerfile`, `.github/`, `tests/contract_test.py`, `testset/run_all.py` |
| A2 Quality | `app/states.py`, `health.py`, `documents.py`, `mock_portal/`, `README.md`, `brain/normalize.py`, `testset/` (except `run_all.py`) |
| B1 Attach + repair | `brain/llm.py`, `shrink.py`, `learn.py`, `repair.py`, `blocks.py` |
| B2 Data + JS | `brain/run_recipe.py`, `js_discover.py` |

Current tasks per role, the timeline and the known problems: plan v2 sections 3 to 5.

## Interfaces (plan 4.2, with one change)

Brain functions get a **PortalFetcher** and a **ScopedLLM**, already bound to one portal.
That way you never pass portal ids around, and costs and rate limits are always counted correctly:

```python
html = (await fetch.get(url)).text           # rate limited, 1 req/s per portal
data = await llm.json(prompt, system="...")  # logged, cached, budget-capped
await fetch.wait_turn(url)                   # Playwright: call before each browser request
```

For offline tests use `FakeFetcher({url: html})` from `app/fetcher.py`.
The hand-written mock recipe is in `testset/fixtures/mock/recipe_v1.json`.
`tests/test_mock_recipe.py` proves it matches layout v1 and breaks on v2.

## Rules

- All HTTP goes through `app/fetcher.py`. All model calls go through `brain/llm.py`.
- Only edit files your role owns. Announce changes to `app/models.py` or the recipe format first.
- Never commit `.env`, `data/` or `testset/secret.yaml`.
- Every fix comes with a test (a mock switch or `FakeFetcher`). Regular reads never call the model.
- Feature stop at 22:00 Friday: after that, fixes only. After 09:00 Saturday a merge needs a second person's OK.
- Coding agents run on subscriptions, never on the hand-in key.

## Git workflow

1. Pull the latest main, then make a short branch per task:
   `git switch main && git pull && git switch -c b2/normalize-dates`
2. Commit small, working steps. Run `pytest` before pushing.
3. Push and open a pull request into `main`. CI runs the tests and `docker build`.
4. Someone from the **other** team takes a 2-minute look, then merge.
5. After someone announces a merge, update your branch: `git pull origin main`.
6. Merge every 1–2 hours, not just at checkpoints. Tag checkpoints: `git tag m1 && git push --tags`.
7. If main breaks, revert the merge at once and fix it on the branch.

## Docker

```bash
docker build -t watchtower .
docker run -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

CI builds and starts the container on every pull request, so you don't need Docker locally.

## Public search recipes

An optional `list.search` object records a search endpoint observed during an anonymous visit, its request media type, and the numeric paging field when present. `list.url` remains the public listing that initiated the search. `html_path` locates HTML fragments inside a JSON envelope, and `total_path` locates an explicit result count. Both paths are arrays of literal keys. Existing HTML and JSON recipes continue to use GET.

During a read, the browser revisits the listing and can scroll to trigger its public search. The reader reuses the observed search fields and that visit's cookies in memory, checks each result batch, and stops on a repeated batch, an incomplete count, or the 100-page limit. The saved recipe contains no request body, cookies or session tokens. Regular reads use no model.
