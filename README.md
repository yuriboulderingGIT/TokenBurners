# Watchtower

Watchtower monitors public tender portals. Give it a listing address in the dashboard or with `POST /portals`. A background worker asks a model for an extraction recipe, tests it on a sample, then follows the listing and downloads its documents. The dashboard shows each portal's state, tenders, files, read history and model spend.

## Run

```sh
cp .env.example .env    # set OPENAI_API_KEY in the local copy
docker build -t watchtower .
docker run -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

Open <http://localhost:8080> for the dashboard or <http://localhost:8080/docs> for the API. The volume keeps SQLite, document bytes and the recipe-response cache across restarts. If reusing an older local `.env`, remove its `DATA_DIR=./data` line before running Docker.

A regular read runs the saved recipe in plain code. Watchtower spaces requests from each portal and to each destination host, including document downloads and redirects. It checks identifiers, field coverage, listing counts and document delivery. If a recipe breaks, the portal enters `healing` and continues to serve its last healthy snapshot. A successful repair restores delivery. Login and captcha gates produce `failed` with a reason.

## Settings

| Environment variable | Default | Use |
|---|---|---|
| `OPENAI_API_KEY` | required | Project key used for learning and repair |
| `OPENAI_MODEL` / `OPENAI_MODEL_STRONG` | `gpt-5-mini` / `gpt-5` | Attach and repair models |
| `LLM_BUDGET_USD` | `5` | Maximum cumulative modeled spend per persistent data folder, including reservations |
| `LLM_CACHE` | `1` | Reuse identical model answers |
| `REFRESH_MINUTES` / `FAILED_RETRY_MINUTES` | `15` / `180` | Healthy refresh and failed-portal retry intervals |
| `WORKER_CONCURRENCY` | `16` | Maximum concurrent portal workers |
| `MIN_REQUEST_INTERVAL` / `HTTP_TIMEOUT` | `1.1` / `30` | Minimum spacing (at least one second) and HTTP timeout in seconds |
| `DATA_DIR` | `/data` in Docker, `./data` locally | Persistent storage |

Use a dedicated funded API project for hand-in. Suggested budgets are $5 for development and a $12 project limit with `LLM_BUDGET_USD=10` for judging. The application budget applies to a single data folder. Give the judges the project key privately. [Project and spending controls](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)

## Measured model cost

On 20 November the real model attached to the mock for **$0.005485** and delivered five tenders with eight documents. A regular refresh of those tenders cost **$0** (zero model calls). After a layout change, one GPT-5 repair cost **$0.028319** and restored the same five IDs and eight documents. The successful Cyprus attach cost **$0.019190** for ten tenders and four documents. These are individual measurements. Across the ten public portals, one reached `serving` and nine failed. The report includes spend on unsuccessful attempts. [A1/A2 live results](testset/results.md) · [Earlier B14 live results](testset/b14-results.md)

With a running container and the Python requirements installed, `python testset/run_all.py` registers the ten public portals and writes the report. Pass `--db PATH` to include measured cost per completed operation. For an isolated run with the mock, use `bash testset/run_session.sh .env data/validation` after building the image. The dashboard uses port 18080 during the run. Reuse the same data folder to preserve the spending ledger.
