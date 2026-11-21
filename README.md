# Watchtower

Give Watchtower the address of a tender portal it has never seen. It learns how to read the portal once, then
delivers every open tender and its documents, checks itself after every read, and repairs itself when the portal
changes. When it cannot deliver, for example behind a login or a captcha, it says so and why.

Built by team TokenBurners for the Patterno track of the MAIN AI Hackathon, Maastricht, November 2026.

## Run it

You need Docker and the OpenAI key we sent you privately.

```sh
docker build -t watchtower .                      # linux/amd64; Chromium is already in the base image
printf 'OPENAI_API_KEY=%s\n' "<the key we sent you>" > .env    # keep this file private, never commit it
docker run --name watchtower --rm -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

Open <http://localhost:8080> for the dashboard and <http://localhost:8080/docs> for the interactive API reference.
The volume keeps the database, the downloaded documents and the model spend ledger across restarts.

To check that the container sees the key (it prints only whether one is set):

```sh
docker exec watchtower python -c 'import os; print("key set" if os.getenv("OPENAI_API_KEY","").strip() else "key MISSING")'
```

## The API

Every answer is JSON, every time has an offset (`2026-12-09T10:00:00+01:00`), and an unknown id answers `404`.

| Call | Answer |
|---|---|
| `POST /portals` with `{"url": "…"}` | `201 {"id": "…"}` at once; the portal is attached in the background. The same address returns the same id. |
| `GET /portals`, `GET /portals/{id}` | `id`, `url`, `state`, `reason` (in words), `tenderCount`, `lastSuccessAt` |
| `POST /portals/{id}/refresh` | `202 {"runId": "…"}`; if a read is already running, its `runId` |
| `GET /portals/{id}/tenders` | Every tender with its documents; `?changedSince=…` for only the changed ones |
| `GET /portals/{id}/runs` | Every read and repair: `kind`, `startedAt`, `finishedAt`, `result` |
| `DELETE /portals/{id}` | `204`; the portal is no longer called |
| `GET /files/{id}` | The bytes of a delivered document (the `path` in each tender's `documents`) |
| `GET /` | The dashboard |

```sh
curl -s -X POST localhost:8080/portals -H 'content-type: application/json' -d '{"url":"https://zakazky.muni.cz/contract_index.html"}'
curl -s localhost:8080/portals
```

## What the states mean

| State | Meaning |
|---|---|
| `integrating` | Learning how to read the portal. Nothing is delivered yet. |
| `serving` | Delivering tenders, and their documents were found and downloaded. |
| `healing` | The last read failed its check. The model is repairing the recipe; the last correct tenders are still served. |
| `failed` | It cannot deliver. The reason says why, for example `blocked: captcha required`, a login, HTTP 403, or a list longer than 100 pages. |

The dashboard shows every portal with its state and reason, what it delivers, and an activity feed of every read and
repair. Each portal's page lists its tenders and files, and what the system did, with each run's notes and model cost.

## How it works

1. **Learn, once.** Watchtower opens the address politely and finds the tender list: server-built HTML, or the JSON feed
   a JavaScript app loads, which it finds with headless Chromium. An OpenAI model (`gpt-5-mini`) writes a *recipe*:
   where the rows are, the next page, each tender's own page, the fields, the documents and the portal's time zone.
   The recipe is tried on the real pages and three sample tenders before it is used. If files are visible but the
   recipe missed them, the model is asked again.
2. **Read, with no model calls.** Every 15 minutes plain Python follows the list and its pages (up to 100), opens
   each tender, and downloads its documents: ZIPs are unpacked, files over 250 MB are left out as the brief allows.
   Deadlines get the portal's time zone; the status comes from the deadline and the portal's own labels.
3. **Check after every read.** Identifiers are stable and unique, the required fields are present, the count matches
   what the portal advertises, and the documents download. A read that fails its check never replaces the last
   correct tenders.
4. **Repair.** When a check fails, the portal goes to `healing` and the stronger model (`gpt-5`) rewrites the recipe
   from the changed page. The new version must pass the same checks before it is used. A login or captcha is not
   repaired: the portal goes to `failed` and says why.

It is polite to every portal: at least 1.1 seconds between requests to the same portal or host, no logins, and no
captcha solving.

## What one portal costs

| Step | Model | Measured cost |
|---|---|---|
| Learn a portal (attach) | `gpt-5-mini` | $0.004 to $0.019 per portal: $0.004 on our mock, $0.008 on Masaryk University (28 tenders, 482 documents), $0.019 on Cyprus |
| Repair after a change | `gpt-5` | $0.017 to $0.028 per repair, one model call |
| Regular read | none | $0 |

Including failed attempts, a live session on four real portals spent $0.14, or $0.035 per portal.
`LLM_BUDGET_USD` (default `5`) stops all model calls once that much has been spent. Sources:
[test set results](testset/results.md), [simulated judging reports](judge/).

## Environment variables

| Variable | Default | Use |
|---|---|---|
| `OPENAI_API_KEY` | required | The OpenAI key for learning and repair |
| `OPENAI_MODEL` / `OPENAI_MODEL_STRONG` | `gpt-5-mini` / `gpt-5` | Models for learning and for repair |
| `LLM_BUDGET_USD` | `5` | Total model spend allowed per data folder; model calls stop at this amount |
| `REFRESH_MINUTES` | `15` | How often a serving portal is read again |
| `FAILED_RETRY_MINUTES` / `REFRESH_REPAIR_MINUTES` | `180` / `60` | Retry interval for failed portals; at most one model repair per this interval |
| `MIN_REQUEST_INTERVAL` / `HTTP_TIMEOUT` | `1.1` / `30` | Seconds between requests to one portal or host (at least 1), request timeout |
| `WORKER_CONCURRENCY` | `16` | Portals worked on at the same time |
| `DOC_REVALIDATIONS_PER_READ` | `30` | Known documents checked again per read, oldest check first |
| `PUBLIC_DESTINATIONS_ONLY` / `MOCK_PORTAL_ADDRESS` | `0` / empty | `1` allows only public Internet addresses; one private `host:port` may stay allowed |
| `LLM_CACHE` | `0` | `1` reuses identical attach answers (development only) |
| `DATA_DIR` | `/data` in Docker | Database, documents and spend ledger |

## What it does not do yet

- A JavaScript-only portal without a public JSON feed is reported `failed`, with the reason.
- Pagination by form posts (ASP.NET postback), and documents behind a button or form, are not followed.
- A list longer than 100 pages is reported `failed`: add the portal with a narrower list, such as open tenders only.
- A portal whose tenders come without any documents is reported `failed`, not `serving`.

## Try it on our mock portal

The repository includes a small portal that can change its layout or put up a captcha on request.

```sh
docker network create wt
docker run -d --name wt-mock --network wt -p 8090:8090 -v "$PWD/mock_portal:/srv/mock_portal:ro" watchtower \
  uvicorn mock_portal.server:app --host 0.0.0.0 --port 8090
docker run -d --name watchtower --network wt -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

Add `http://wt-mock:8090/tenders` in the dashboard and wait for `serving` (about a minute). Then open
<http://localhost:8090/_switch?layout=v2> and choose **Read again now**: the portal goes to `healing`, then back to
`serving` with recipe v2 and the same tender IDs. <http://localhost:8090/_switch?variant=captcha> leads to `failed`,
and <http://localhost:8090/_switch?reset=1> puts everything back.

## Develop and test

```sh
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                                   # unit and fixture tests, no model calls
bash tests/container_contract.sh watchtower # the public API contract against the built image (needs Docker)
python -m brain.proof --rehearse            # attach, layout change, repair and captcha on the mock, offline
```

| Folder | What is in it |
|---|---|
| `app/` | API, dashboard, worker, health checks, documents |
| `brain/` | Learning, reading and repairing recipes |
| `mock_portal/` | The test portal with its switches |
| `tests/`, `testset/`, `judge/` | Tests, the real-portal test set with results, simulated judging |
| `pitch-video/` | Our pitch video, made as code |
