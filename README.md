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
   with a reason in plain words.

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
| `LLM_BUDGET_USD` | no | `5` | Total model budget; calls stop beyond it |
| `REFRESH_MINUTES` | no | `15` | How often each portal is re-read |
| `FAILED_RETRY_MINUTES` | no | `180` | How often a failed portal is retried (plain read, no repair) |
| `WORKER_CONCURRENCY` | no | `4` | Portals read at the same time |
| `MIN_REQUEST_INTERVAL` | no | `1.1` | Seconds between requests to one portal (rule: at least 1) |
| `HTTP_TIMEOUT` | no | `30` | Seconds before one HTTP request gives up |
| `DATA_DIR` | no | `/data` | Where the database and documents are stored |

## Model cost per portal

TODO(A2): measured from the `llm_calls` table: attach $…, repair $…, regular read $0.
