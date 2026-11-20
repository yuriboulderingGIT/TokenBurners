# Watchtower — TokenBurners · Patterno track · MAIN Hackathon 2026

<!-- OWNER: A2 (P11). One page for the judges. Fill in the TODOs with measured numbers. -->

Give Watchtower the address of a public-tender portal it has never seen. It delivers every
open tender with its documents, notices when the portal stops delivering correctly, and
repairs itself without any human help.

## How it works

1. **Attach (LLM, once per portal):** explore the portal and write a *recipe*, i.e. XPaths
   or a JSON API description, then test it on 3 tenders.
2. **Read (no LLM):** plain code runs the recipe, cleans the fields and downloads documents.
   Regular reads cost nothing.
3. **Health check after every read.** Only a healthy read leads to `serving`.
4. **Repair (LLM):** when checks fail, the portal goes to `healing`. The model rewrites the
   recipe (max 3 tries) while the API keeps serving the last correct tenders.
   If it can't be repaired, or a login or captcha blocks it, the portal goes to `failed`
   with a reason in plain words. A crash during repair also ends in `failed`, never a stuck
   `healing`. Failed portals get a plain re-read (no model cost) every `FAILED_RETRY_MINUTES`.
5. **Deadlines are live:** an open tender whose deadline has passed is served as `closed`
   right away, even while its portal is `healing` or `failed`. Its `lastChangedAt` is the
   deadline, so `changedSince` clients see the change.

Posting the same portal address twice (also with or without a trailing `/`) returns the same id.
A refresh while a read or repair is running returns that run's id.

## Run

```bash
docker build -t watchtower .
docker run -p 8080:8080 -e OPENAI_API_KEY=... -v watchtower-data:/data watchtower
```

API and dashboard: http://localhost:8080. Everything (SQLite database, documents, LLM cache)
lives in `/data`; mount a volume to keep it across restarts.

**Politeness:** every HTTP request, including each redirect hop, document download and browser
request, goes through one rate limiter: at most 1 request per second per portal.

## Environment variables

| Name | Required | Default | Meaning |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | | OpenAI key (handed over at submission) |
| `OPENAI_MODEL` | no | `gpt-5-mini` | Cheap model for attach |
| `OPENAI_MODEL_STRONG` | no | `gpt-5` | Strong model for repair |
| `LLM_BUDGET_USD` | no | `5` | Shared budget for completed and in-flight model calls |
| `REFRESH_MINUTES` | no | `15` | How often each portal is re-read |
| `FAILED_RETRY_MINUTES` | no | `180` | How often a failed portal is retried (plain read, no repair) |
| `WORKER_CONCURRENCY` | no | `4` | Portals read at the same time |
| `MIN_REQUEST_INTERVAL` | no | `1.1` | Seconds between requests to one portal (rule: at least 1) |
| `HTTP_TIMEOUT` | no | `30` | Seconds before one HTTP request gives up |
| `DATA_DIR` | no | `/data` | Where the database and documents are stored |

## Model cost per portal

Each model request reserves its maximum estimated cost in SQLite. Once OpenAI reports
usage, Watchtower records the cost and releases the reservation. If a request is
interrupted before usage is known, its reservation stays in `llm_reservations` across
restarts. The default models and their dated snapshots have configured prices in
`brain/llm.py`. Additional models need a price entry before they can spend the budget.

TODO(A2): measured from the `llm_calls` table: attach $…, repair $…, regular read $0.
