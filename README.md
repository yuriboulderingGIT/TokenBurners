# Watchtower

Watchtower monitors public tender portals. Give it a listing address in the dashboard or with `POST /portals`. A background worker asks a model for an extraction recipe, tests it on a sample, then follows the listing and downloads its documents. The dashboard shows each portal's state, tenders, files, read history and model spend.

## Run

```sh
test -f .env || cp .env.example .env
# Edit .env and set OPENAI_API_KEY before starting the container.
docker build -t watchtower .
docker run -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

Open <http://localhost:8080> for the dashboard or <http://localhost:8080/docs> for the API. The volume keeps SQLite, document bytes and the recipe-response cache across restarts. If reusing an older local `.env`, remove its `DATA_DIR=./data` line before running Docker.

If a new portal reports “OpenAI client could not start”, check the key inside the container serving your dashboard. Find its name with `docker ps` and run the following check, replacing `watchtower-local` with that name. It prints only whether a value is configured.

```sh
docker exec watchtower-local python -c 'import os; print("Key configured" if os.getenv("OPENAI_API_KEY", "").strip() else "Key missing")'
```

If the key is missing, set it in your private `.env`, then recreate the container with `--env-file .env` and the same port and data volume. Docker reads the env file when it creates the container. Once it is running, open the failed portal and choose **Read again now**.

A regular read runs the saved recipe in plain code. Watchtower spaces requests from each portal and to each destination host, including document downloads and redirects. It checks identifiers, field coverage, listing counts and document delivery. If a recipe breaks, the portal enters `healing` and continues to serve its last healthy snapshot. A successful repair restores delivery. Login and captcha gates produce `failed` with a reason.

Each read checks up to 30 known documents again, starting with those checked longest ago (`DOC_REVALIDATIONS_PER_READ`). A check uses the ETag or Last-Modified date. A file with neither is downloaded again after 15 minutes, and any file is downloaded in full after 24 hours. A revised document gets a new file link, and older links keep serving their revision. If a check fails, the last verified file stays in the delivery.

A tender the portal no longer lists is kept and marked `closed`. A listing that only shows the latest results also drops tenders that are still open. There a tender stays `open` until its own page shows it closed or withdrawn, or its deadline passes. The dashboard shows since when each tender has been absent from the listing.

## Find a tender website to try

For a first test, open the Masaryk University listing in a private browser window. Pick a notice and note its title and reference. Paste the **listing URL** into Watchtower. Once it says `serving`, open a tender in the dashboard and compare it with the original notice. Our local run served 15 notices from Masaryk and 10 from Cyprus. [Masaryk University procurement](https://zakazky.muni.cz/) · [Cyprus latest calls for tenders](https://www.eprocurement.gov.cy/epps/quickSearchAction.do?searchType=cftFTS&latest=true)

1. Search for a buyer and its current opportunities. Try `university procurement "current tenders"`, `site:.cz "veřejné zakázky" univerzita`, or `commune "marchés en cours"`. For an EU notice, follow the buyer profile or procurement documents link on TED to find the original portal. [Tenders Electronic Daily](https://ted.europa.eu/)
2. On the buyer’s site, follow **Open tenders**, **Contract notices**, or the local equivalent until you see several named opportunities. Copy that results page’s address, including its query string. Check that reopening the address in a private window reproduces the list and that a notice opens without signing in.
3. Look for a reference number and title on individual notices. If there are multiple pages, check whether **Next** has a link you can copy and reopen. A small buyer listing with public detail links is easier to verify than a national search with hundreds of pages. Watchtower checks up to 100 pages.
4. Paste the listing address into the dashboard. If the read fails, open the portal’s history. HTTP 403 means the site refused Watchtower’s request. A CAPTCHA requires human interaction. For either outcome, look for another public listing published by the buyer. JavaScript listings need an anonymous JSON feed Watchtower can read. An OpenAI client startup error points to the running container’s key configuration and can affect any new portal.

If a site opens on a search form, Watchtower can use the empty results URL from a public
GET search that accepts a blank keyword. It also follows supported GET pagination controls.
If the site requires a keyword, run your search first and paste its shareable results URL.
Watchtower monitors that result set. Tender counts and downloaded document counts are
reported separately because some portals make their notices public but restrict their files.

## Settings

| Environment variable | Default | Use |
|---|---|---|
| `OPENAI_API_KEY` | required | Project key used for learning and repair |
| `OPENAI_MODEL` / `OPENAI_MODEL_STRONG` | `gpt-5-mini` / `gpt-5` | Attach and repair models |
| `LLM_BUDGET_USD` | `5` | Maximum cumulative modeled spend per persistent data folder, including reservations |
| `LLM_CACHE` | `1` | Reuse identical model answers |
| `REFRESH_MINUTES` / `FAILED_RETRY_MINUTES` | `15` / `180` | Healthy refresh and failed-portal retry intervals |
| `REFRESH_REPAIR_MINUTES` | `60` | A refresh of a failed portal starts a model repair or a new attach at most this often |
| `DOC_REVALIDATIONS_PER_READ` | `30` | Known documents checked again per read, stalest first |
| `WORKER_CONCURRENCY` | `16` | Maximum concurrent portal workers |
| `MIN_REQUEST_INTERVAL` / `HTTP_TIMEOUT` | `1.1` / `30` | Minimum spacing (at least one second) and HTTP timeout in seconds |
| `PUBLIC_DESTINATIONS_ONLY` | `0` | `1` limits portal requests to public Internet addresses (for public deployments) |
| `MOCK_PORTAL_ADDRESS` | empty | With `PUBLIC_DESTINATIONS_ONLY=1`: one trusted private host and port, such as `localhost:8090` |
| `DATA_DIR` | `/data` in Docker, `./data` locally | Persistent storage |

Portals may run on a private network, so Watchtower reaches any address by default. For a public deployment, set `PUBLIC_DESTINATIONS_ONLY=1`. Watchtower then checks all DNS answers for each new connection and connects only to a checked public address. Redirects, search feeds, browser resources and file downloads use the same transport. Also restrict outbound traffic from the container to public destinations and the deployment's trusted DNS resolver at the network layer.

Use a dedicated funded API project for hand-in. Suggested budgets are $5 for development and a $12 project limit with `LLM_BUDGET_USD=10` for judging. The application budget applies to a single data folder. Give the judges the project key privately. [Project and spending controls](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)

## Measured model cost

On 20 November the real model attached to the mock for **$0.005485** and delivered five tenders with eight documents. A regular refresh of those tenders cost **$0** (zero model calls). After a layout change, one GPT-5 repair cost **$0.028319** and restored the same five IDs and eight documents. The successful Cyprus attach cost **$0.019190** for ten tenders and four documents. These are individual measurements. Across the ten public portals, one reached `serving` and nine failed. The report includes spend on unsuccessful attempts. [A1/A2 live results](testset/results.md) · [Earlier B14 live results](testset/b14-results.md)

With a running container and the Python requirements installed, `python testset/run_all.py` registers the ten public portals and writes the report. Pass `--db PATH` to include measured cost per completed operation. For an isolated run with the mock, use `bash testset/run_session.sh .env data/validation` after building the image. The dashboard uses port 18080 during the run. Reuse the same data folder to preserve the spending ledger.
