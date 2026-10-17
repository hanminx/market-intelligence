# Market Intelligence

A local research application that helps organize US stock-market news around a
personal watchlist. It collects financial news, preserves source articles in
SQLite, and optionally uses AI to produce structured, auditable article-level
judgments for a server-rendered news interface.

**Local use only:** this application has no authentication and no publicly hosted
production instance. Run it on localhost; do not expose it directly to the Internet.

## Tech Stack

Python · FastAPI · Jinja2 · SQLite · httpx · Pydantic · OpenAI Responses API

## Architecture

```text
Massive → ingestion → SQLite → optional AI scoring → FastAPI/Jinja interface
```

The interface reads stored data; opening a page never triggers external API calls.
News collection and AI scoring are separate, manually invoked commands.

## Engineering Highlights

- **Incremental ingestion:** collect a bounded, recent news window for watched
  tickers, validate each ticker's complete batch, and save it transactionally.
- **Provider isolation and deduplication:** normalize provider responses behind a
  shared interface, deduplicate by provider/article identity, and preserve ticker
  associations independently of watchlist edits.
- **Validated/versioned AI judgments:** enforce a strict typed output contract and
  surfacing rules, retain source data separately, and version judgments by model,
  prompt, schema, and request settings with saved evidence for auditability.

The project includes stock/sector watchlists, a news archive, and optional AI
triage. It does not include event clustering, embeddings, price monitoring,
background jobs, or trading recommendations. Model scoring accuracy has not been
established by the automated tests.

Detailed setup, testing, schemas, and scoring policies follow below. API keys and
local databases are excluded from version control; `.env.example` contains only
placeholders.

## Local setup and run

Requires Python 3.10 or newer. Run these commands from the project root on macOS
or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 to see **Market Intelligence** and **System is running**.
Follow **Manage watchlist** or open http://127.0.0.1:8000/watchlist.
Read collected articles at http://127.0.0.1:8000/news.
Stop the server with `Ctrl+C`. The `--reload` flag is for local development.

The web app requires no API key and never calls Massive or OpenAI when viewing a
page. The news collector requires `MASSIVE_API_KEY`; the separate AI scorer
requires `OPENAI_API_KEY`. See the manual commands below.

## Verify

With the server running, open http://127.0.0.1:8000/health or run:

```sh
curl http://127.0.0.1:8000/health
```

Expected response (HTTP 200):

```json
{"status":"ok"}
```

The homepage and watchlist should fit a mobile viewport without horizontal
scrolling. On `/watchlist`, add ` aapl ` and ` Technology `; the lists show `AAPL`
and `Technology`. Try adding them again to see a duplicate error, then use each
row's **Remove** button. Add entries, stop and restart the server, and verify
that the entries remain.

To submit forms from a terminal while the server is running:

```sh
curl -i --data-urlencode 'symbol= aapl ' http://127.0.0.1:8000/watchlist/tickers
curl -i --data-urlencode 'name= Technology ' http://127.0.0.1:8000/watchlist/sectors
curl http://127.0.0.1:8000/watchlist
```

Successful writes return `303 See Other` with `Location: /watchlist`. Duplicate
or blank values return an HTML error page with HTTP 400. These commands modify
your local watchlist; use the page's Remove buttons to clean up.

Run the automated tests from the project root with the virtual environment active:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests use temporary SQLite files and never modify `data/intelligence.db`. They
cover the existing homepage and health route, empty lists, input normalization,
duplicates, blank/missing inputs, removal, HTML escaping, and persistence across
application restarts. News tests cover mocked HTTP requests, normalization,
pagination, failures, deduplication, relationships, additive schema setup, and
the collector and news page. AI tests use deterministic fake scorers and the
actual OpenAI SDK with an HTTP mock transport. They cover typed validation,
refusals, partial failures, versioning, raw-data preservation, idempotency,
concurrent-run exclusion, CLI summaries, and filtered pagination. No tests make
external API calls or need a real key. These tests verify the integration and
policies; they do not establish live-model scoring accuracy.

## Watchlist behavior and persistence

- Ticker symbols are trimmed and converted to uppercase. Empty values and
  duplicates are rejected. Symbols are not checked against an exchange or API.
- Sector names are trimmed and retain their capitalization. Empty values and
  duplicates are rejected. SQLite's `NOCASE` comparison ignores ASCII letter
  case, so `Technology` and `technology` are duplicates.
- Both lists are sorted alphabetically. Every item has a Remove button.
- Forms use standard HTML POST submissions, with no JavaScript. FastAPI reads
  form fields using `python-multipart`; the database layer validates and writes
  the values. Successful submissions redirect with HTTP 303 to a fresh GET of
  `/watchlist`, preventing refresh from repeating a successful write. Errors
  render the watchlist with the submitted value and a readable message.
- Removal uses POST, not GET. Removing an already-removed ID is harmless.

At startup, the app creates `data/` and `data/intelligence.db` if needed, then
creates any missing tables. The path is resolved relative to the project,
not the shell's working directory. Startup never clears existing entries.
Each database operation opens and closes its own connection; successful writes
commit and failures roll back. SQL values use bound parameters.

| Table | Columns |
| --- | --- |
| `watched_tickers` | `id INTEGER PRIMARY KEY`, `symbol TEXT NOT NULL UNIQUE` (uppercase, nonempty), `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP` |
| `watched_sectors` | `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL COLLATE NOCASE UNIQUE` (nonempty), `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP` |

SQLite assigns IDs and UTC creation timestamps. The database file survives
server restarts. `data/` and `*.db` remain ignored by Git, so entries are local
and are not included in commits. Python's built-in `sqlite3` is the only database
library; no database server or manual setup is needed.

## Routes

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/` | Existing homepage, now with watchlist navigation |
| GET | `/health` | Existing `{"status":"ok"}` response |
| GET | `/watchlist` | Display both lists and forms |
| GET | `/news` | All stored articles and current-version AI judgments; `?view=surfaced` filters, `&page=2` paginates |
| POST | `/watchlist/tickers` | Add the `symbol` form field |
| POST | `/watchlist/tickers/{ticker_id}/remove` | Remove a ticker by its integer ID |
| POST | `/watchlist/sectors` | Add the `name` form field |
| POST | `/watchlist/sectors/{sector_id}/remove` | Remove a sector by its integer ID |

## Collect news manually

1. Start the web app and add stocks at `/watchlist`.
2. In another terminal, activate the same environment and set your Massive key:

   ```sh
   cd /path/to/market-intelligence
   source .venv/bin/activate
   export MASSIVE_API_KEY="your-key-from-massive"
   python -m app.news.collector
   ```

   In Windows PowerShell, set the key with:

   ```powershell
   $env:MASSIVE_API_KEY = "your-key-from-massive"
   python -m app.news.collector
   ```

3. Open or refresh http://127.0.0.1:8000/news.

The default collection window is the past **7 days**, measured in UTC. To use
a different window:

```sh
python -m app.news.collector --days 3
python -m app.news.collector --help
```

`--days` accepts 1–365. The collector also works while the web server is stopped.
It creates missing tables automatically without changing existing watchlists.
An empty ticker watchlist exits successfully with instructions and makes no
request. Sector watchlists are preserved but are not news query inputs yet.

The collector reads `MASSIVE_API_KEY` directly from the process environment.
`.env.example` documents the name; `.env` is **not loaded automatically**. If you
prefer a local file, copy `.env.example` to `.env`, fill in the key, and export it
before collecting (macOS/Linux, from the project root):

```sh
set -a
source .env
set +a
python -m app.news.collector
```

The key is sent in an Authorization header, not a query parameter. Error messages
do not print request URLs, raw provider responses, or credentials. `.env`,
environment variants, `data/`, and `*.db` remain ignored by Git.

Collection prints attempted tickers, fetched article occurrences, newly inserted
articles, already-stored occurrences, and failed tickers. The same article may
be counted as fetched for multiple tickers, but is stored only once per provider.

- Exit **0**: collection succeeded, including no results or an empty watchlist.
- Exit **1**: missing key, provider/network/response failure, or storage failure.
- Exit **2**: invalid command arguments.

Each ticker uses `ticker`, `published_utc.gte`, `sort=published_utc`, and
`order=desc` on Massive's `/v2/reference/news` endpoint. Pages contain up to 100
results; the adapter follows `next_url` until complete. A 20-second request
timeout and a 100-page-per-ticker guard bound requests. Repeated or invalid
pagination URLs are errors; credentials are never forwarded to another host.

All pages for one ticker must validate before its results are saved in a single
transaction. If that ticker fails, none of its fetched batch is saved and the
collector continues with the next ticker. Earlier successful batches remain.
HTTP 401/403 explains key/access problems; HTTP 429 reports rate limiting;
network failures and malformed JSON/articles produce readable errors. There
are no automatic retries: after a transient failure or rate limit, wait and
rerun. Reruns are safe because database uniqueness prevents duplicate articles.
If the pagination guard is reached, rerun with a smaller `--days` window.

Provider contract and authentication references:
[Massive Stocks News](https://massive.com/docs/rest/stocks/news) and
[REST authentication](https://massive.com/docs/rest/quickstart).

## News architecture and schema

`NewsProvider` is a small Python protocol with one method:
`fetch_news(ticker, since) -> list[Article]`. `Article` is a frozen dataclass
containing provider identity, article identity, headline, summary, source URL,
publisher, an aware publication timestamp, and a tuple of ticker symbols.
Providers return normalized articles or raise a safe `ProviderError`.

`MassiveNewsProvider` alone knows Massive's URLs, JSON field names, authentication,
and pagination. It maps `description` to summary and `published_utc` to UTC,
trims text, uppercases/deduplicates ticker symbols, and validates required fields
and HTTP(S) source links. Missing descriptions become an empty summary; the page
shows a fallback. Sentiment/insight fields are ignored. A replacement provider
implements the same protocol; only the command's provider construction changes,
not the collection logic, database queries, or page.

Two new tables share `data/intelligence.db` with the watchlists:

| Table | Columns and constraints |
| --- | --- |
| `news_articles` | `id INTEGER PRIMARY KEY`; non-null text columns `provider`, `provider_article_id`, `title`, `summary`, `url`, `publisher`, `published_at`, `fetched_at`; `UNIQUE(provider, provider_article_id)` |
| `news_article_tickers` | `article_id` foreign key to `news_articles.id` with cascading deletion; `symbol TEXT NOT NULL`; composite primary key `(article_id, symbol)` |

There is one relationship row per article/ticker pair, not a serialized ticker
list. All provider-associated tickers are stored, including those outside the
current watchlist. Associations intentionally do not reference `watched_tickers`:
removing a stock from the watchlist must not erase historical article context.
Foreign keys are enabled on each connection.

Article writes use `ON CONFLICT(provider, provider_article_id) DO NOTHING`.
Duplicates preserve the original content and first `fetched_at`; newly discovered
ticker associations are merged. Identical URLs from different providers are not
merged. `published_at` and `fetched_at` use fixed-width ISO 8601 UTC text so date
ordering is consistent, including timestamps received with different offsets.
An index supports ordering by publication time and ID.

`GET /news` reads the stored archive, newest first, with 50 articles per page.
It includes publication time (UTC), all related tickers, publisher, headline,
provider summary, and the original source link. It does not fetch articles,
filter out history after watchlist edits, or generate summaries. Jinja escapes
provider text. Responsive cards, wrapping ticker badges, and accessible links
support mobile screens.

## Files

See the AI scoring section below for the analysis package and commands.

```text
app/
  __init__.py
  database.py
  main.py
  watchlist.py
  analysis/
    __init__.py
    schema.py
    prompt_v1.txt
    config.py
    provider.py
    openai_provider.py
    repository.py
    locking.py
    scorer.py
  news/
    __init__.py
    provider.py
    massive.py
    repository.py
    collector.py
    routes.py
  templates/
    index.html
    watchlist.html
    news.html
tests/
  test_watchlist.py
  test_news.py
  test_news_provider.py
  test_analysis.py
  test_openai_scorer.py
data/
  intelligence.db       # Created on startup; ignored by Git
.env.example
.gitignore
requirements.txt
requirements-dev.txt
README.md
```

- `app/__init__.py` marks the application as a Python package.
- `app/database.py` owns the SQLite schema, connections, validation, and list/add/remove operations.
- `app/main.py` creates FastAPI, initializes SQLite at startup, registers the watchlist and news routers, and defines `GET /` and `GET /health`.
- `app/watchlist.py` defines the watchlist routes and handles form responses; no SQL is in the route functions.
- `app/templates/index.html` is the server-rendered Jinja2 homepage with responsive inline CSS. No JavaScript or external assets are required.
- `app/templates/watchlist.html` provides mobile-friendly lists, labeled add forms, removal buttons, and error messages.
- `app/news/provider.py` defines the normalized article and replaceable provider contract.
- `app/news/massive.py` handles Massive HTTP requests and normalization.
- `app/news/repository.py` stores and queries articles and ticker relationships.
- `app/news/collector.py` provides collection orchestration and the manual CLI.
- `app/news/routes.py` and `app/templates/news.html` serve the paginated news page.
- `requirements.txt` lists FastAPI, Uvicorn, Jinja2, python-multipart, httpx, OpenAI's SDK, and Pydantic (the typed analysis contract).
- `requirements-dev.txt` includes runtime requirements; no additional test packages are needed. Tests use Python's built-in unittest runner.
- `tests/test_watchlist.py` contains regression and persistence tests with isolated temporary databases.
- `tests/test_news.py` and `tests/test_news_provider.py` test news storage, collection, pages, and mocked provider responses.
- `.env.example` documents the collection/scoring API keys and scoring model. Keys are read only from the process environment.
- `.gitignore` excludes environment files, virtual environments, Python artifacts, SQLite databases and sidecar files, and local data directories. Store future local data in `data/` or `local_data/`.
- `README.md` contains setup, run, and verification instructions.

## Score articles manually (Milestone 3)

First collect news with the existing command. Then, from the project root with
your virtual environment active:

```sh
python -m pip install -r requirements.txt
export OPENAI_API_KEY="your-openai-api-key"
python -m app.analysis.scorer --limit 20
```

For Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-openai-api-key"
python -m app.analysis.scorer --limit 20
```

The key is loaded from `os.environ`; it is not stored in SQLite or logged.
`.env` is not automatically loaded. The earlier `set -a; source .env; set +a`
procedure also works for the new variables documented in `.env.example`.
Calling the scorer sends the article's stored metadata/headline/summary and the
watched-ticker snapshot to OpenAI. It does not fetch the full article or use tools.

`--limit` defaults to 20, accepts 1–1000, and limits **pending articles attempted**,
newest first. Scoring runs only when this command is invoked. It can run with
the web server stopped. For usage, run:

```sh
python -m app.analysis.scorer --help
```

Example summary:

```text
Articles attempted: 20
scored: 19
already scored: 5
failed: 1
surfaced: 6
```

`already scored` counts articles in the archive with the current model/prompt
identity that were skipped; it is not limited to the current batch. `surfaced`
counts newly saved surfaced analyses in this run. Failures print the article ID
and a safe message to stderr. OpenAI exceptions include sanitized exception class,
HTTP status, API error code/parameter/message, and request ID when available.
Only those fields are extracted; credentials and authorization headers are redacted,
text is bounded, and requests/full error bodies are never dumped.

Exit codes: **0** for success/no pending work, **1** for configuration, API,
validation, storage or concurrent-run failure, **2** for invalid CLI arguments.
No key is needed for a no-work run. A missing key with pending work is an error.

Each successful article is committed immediately in its own transaction. Refused,
incomplete, malformed or inconsistent output creates no analysis row. The command
continues after individual failures; previously committed successes stay stored.
The SDK has a 30-second timeout and automatic retries disabled. Rerunning attempts
failed/unscored articles while skipping successes. Persistently failing recent
articles count toward the limit; increase it if needed to reach older pending work.

The scorer uses a separate `data/scoring.lock.db` file solely for a SQLite process
lock, preventing overlapping commands from making duplicate paid requests. The
operating system releases it on exit/crash; the file may remain and requires no
cleanup. It does not lock the article database during API requests, so the web
app and watchlist editing remain usable. It is ignored by Git under `data/`.

## AI architecture and Structured Outputs

- `analysis/schema.py`: strict Pydantic `ArticleAnalysis`; extra fields, wrong
  types, out-of-range scores, invalid enums, blank explanations, duplicate/lowercase
  primary tickers, and inconsistent surfacing flags are rejected.
- `analysis/prompt_v1.txt`: source-controlled rubric and untrusted-content rules.
- `analysis/config.py`: model selection and prompt/schema/settings fingerprint.
- `analysis/provider.py`: `ArticleScorer` protocol and provider-neutral result.
- `analysis/openai_provider.py`: the sole OpenAI adapter, using
  `client.responses.parse(..., text_format=ArticleAnalysis)` on the Responses API.
  It checks completion/refusal status and revalidates the parsed object.
- `analysis/repository.py`: version-aware pending queries, immutable writes, and
  the raw-article/current-analysis join used by `/news`.
- `analysis/locking.py`: one local scoring run at a time.
- `analysis/scorer.py`: input snapshots, orchestration, per-article failures and CLI.

The protocol accepts the exact serialized input snapshot and returns a typed
analysis with response metadata. A different provider can implement that contract
without changing source storage or templates. Provider-specific model identifiers
must remain distinct when switching vendors, since the uniqueness key uses model.

| Field | Type / allowed values |
| --- | --- |
| `primary_tickers` | Unique uppercase string list; supported primary subjects from the source ticker associations |
| `ticker_relevance_score` | Integer 0–100, relevance to the saved watched-ticker snapshot |
| `content_type` | `company_event`, `sector_event`, `macro`, `analyst`, `opinion`, `market_recap`, `evergreen`, `promotional`, `other` |
| `novelty_score` | Integer 0–100 |
| `fundamental_impact_score` | Integer 0–100 |
| `market_impact_score` | Integer 0–100 |
| `importance_score` | Integer 0–100 |
| `impact_direction` | `positive`, `negative`, `mixed`, `neutral`, `unclear` |
| `time_horizon` | `immediate`, `short`, `medium`, `long` |
| `is_opinion`, `should_surface` | Booleans |
| `why_it_matters` | Nonblank explanation, maximum 600 characters |
| `confidence_score` | Integer 0–100 |

References: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [GPT-4.1 mini snapshots](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

## Scoring rubric

The scorer distinguishes concrete new earnings/guidance, deals, regulation,
litigation and industry developments from generic investment commentary,
historical-return articles, valuation listicles, ETF holdings mentions, promotional
research releases and ordinary catalyst-free price recaps. Those noise categories
normally receive importance <=25 and novelty <=20. Publisher prestige, hype,
article length and a large past stock move do not establish importance.

Watchlist relevance ranges from 0 for none, 1–20 for incidental/tag-only mentions,
21–49 for secondary mentions, 50–79 for substantive exposure, and 80–100 for a
watched company as the central subject. Sector/macro items need a supported
transmission channel to watched companies. Empty watchlists require zero relevance.

Importance ranges from noise (0–20), low-value context (21–39), relevant but not
material/new enough (40–59), material new developments (60–79), to exceptional
high-consequence information (80–100). Fundamental and market impacts are separate
judgments. Missing/thin evidence lowers confidence; explanations must acknowledge it.

`should_surface` is true **only when all** of these conditions hold:

- Importance >=60, novelty >=50, watchlist relevance >=60, and confidence >=60.
- `is_opinion` is false.
- Content type is `company_event`, `sector_event`, `macro`, or `analyst`.

The prompt requests this exact rule; local validation rejects inconsistent output
instead of rewriting the returned judgment. The explanation describes the catalyst
and mechanism, or why the article is noise, without inventing facts or recommending
trades. Source text is untrusted evidence, never instructions to the scorer.

Novelty is estimated **within each supplied article**. There is no cross-article
comparison, event memory or clustering, so separate stories about the same event
can both surface. Only stored titles/summaries are available, not full articles.

## Analysis storage, versioning and idempotency

`article_analyses` is added automatically at startup or command execution. Existing
watchlists, `news_articles`, and source ticker relationships are not modified.
The new table has:

- `id`, `article_id` (foreign key to source article), all structured fields above,
  `model`, `prompt_version`, and UTC `scored_at`.
- `primary_tickers` as a validated JSON array; this is an AI judgment and does not
  replace the relational provider ticker associations.
- Audit fields: `provider`, `input_json`, `input_sha256`, `prompt_text`,
  `schema_json`, `request_settings_json`, `response_model`, and `response_id`.
- `UNIQUE(article_id, model, prompt_version)`, score/boolean/enum constraints,
  and an index supporting current-version surfaced filtering.

The evidence snapshot contains the raw article and ticker associations plus the
watched tickers at scoring time. It is the exact user message sent to OpenAI.
The prompt, typed schema and request settings are saved with each success; this
small duplication keeps each judgment independently auditable. The requested model
and the model reported by the API are both retained.

Default model: **`gpt-4.1-mini-2025-04-14`**. Override it for both scorer and web
server by exporting `OPENAI_MODEL` in their environments:

```sh
export OPENAI_MODEL="gpt-4.1-mini-2025-04-14"
```

To use GPT-5.6 Luna, export `OPENAI_MODEL=gpt-5.6-luna` in both the scorer and web
server environments. GPT-5.6 and its variants use `reasoning={"effort":"low"}`,
`max_output_tokens=2000`, and `store=false`, with no `temperature` parameter.
The existing GPT-4.1 path retains `temperature=0`, `max_output_tokens=2000`, and
`store=false`. Model-specific settings are saved and fingerprinted as before;
the GPT-4.1 fingerprint is unchanged. The rubric and schema are unchanged.
Choose a model supporting Responses Structured Outputs and its request settings.
Prefer pinned snapshots to moving aliases. Snapshots and stored inputs improve
auditability/repeatability; they do not guarantee bit-identical model outputs.

The prompt identity is `article-triage-v1-<fingerprint>`, calculated from the exact
prompt text, Pydantic schema and request settings. Editing those changes the
identity, preventing silent reuse. Keep future rubric versions in versioned files;
when changing local validation or input-building behavior, revise the rubric/version
as well. No prompt-version override or force-rescore flag is provided.

Pending selection excludes already-scored `(article_id, model, prompt_version)`
combinations before making API calls; inserts also enforce uniqueness without
overwriting earlier analyses. A new model/prompt permits a separate analysis only
when the manual scorer is run. Watchlist changes alone do **not** trigger rescoring:
old judgments remain tied to their saved context. Earlier versions stay in SQLite.

`/news` defaults to **All articles**, including unscored and low-scoring source data.
**Surfaced / important** uses `?view=surfaced` and filters before pagination.
Both views retain original headlines, summaries, ticker tags and source links.
AI importance, content type, direction and explanation are clearly labeled;
expandable details show component scores and provenance. The page displays only
the configured current model/prompt; it never silently falls back to older results
and never makes an AI call. An older-only analysis is shown as not scored for the
current version; its record remains available in SQLite for audit.
