# Market Intelligence

A minimal, mobile-first foundation for a personal US stock market research system.
The app provides persistent stock and sector watchlists and manually collected
financial news, using SQLite and server-rendered HTML. Massive is the first news
provider. No AI scoring, OpenAI, price monitoring, authentication, or background
jobs are included.

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

The web app requires no API key and never calls Massive when viewing a page.
Only the manual news collector requires `MASSIVE_API_KEY`; see below.

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
the collector and news page. No tests make external API calls or need a key.

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
| GET | `/news` | Stored articles, newest first; optional `?page=2` pagination |
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

```text
app/
  __init__.py
  database.py
  main.py
  watchlist.py
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
- `requirements.txt` lists FastAPI, Uvicorn, Jinja2, python-multipart, and httpx (HTTP requests and the test client).
- `requirements-dev.txt` includes runtime requirements; no additional test packages are needed. Tests use Python's built-in unittest runner.
- `tests/test_watchlist.py` contains regression and persistence tests with isolated temporary databases.
- `tests/test_news.py` and `tests/test_news_provider.py` test news storage, collection, pages, and mocked provider responses.
- `.env.example` documents `MASSIVE_API_KEY`, required only for collection.
- `.gitignore` excludes environment files, virtual environments, Python artifacts, SQLite databases and sidecar files, and local data directories. Store future local data in `data/` or `local_data/`.
- `README.md` contains setup, run, and verification instructions.
