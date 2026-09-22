# Market Intelligence

A minimal, mobile-first foundation for a personal US stock market research system.
Milestone 1 adds a persistent stock and sector watchlist using SQLite and
server-rendered HTML. No AI services, external data APIs, authentication, or
background jobs are included.

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
Stop the server with `Ctrl+C`. The `--reload` flag is for local development.

No environment configuration is needed. `.env.example` is a placeholder for
future settings; copying it to `.env` is optional, and the app does not load it.

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
application restarts.

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
creates the two tables if absent. The path is resolved relative to the project,
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
| POST | `/watchlist/tickers` | Add the `symbol` form field |
| POST | `/watchlist/tickers/{ticker_id}/remove` | Remove a ticker by its integer ID |
| POST | `/watchlist/sectors` | Add the `name` form field |
| POST | `/watchlist/sectors/{sector_id}/remove` | Remove a sector by its integer ID |

## Files

```text
app/
  __init__.py
  database.py
  main.py
  watchlist.py
  templates/
    index.html
    watchlist.html
tests/
  test_watchlist.py
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
- `app/main.py` creates FastAPI, initializes SQLite at startup, registers the watchlist router, and defines `GET /` and `GET /health`.
- `app/watchlist.py` defines the watchlist routes and handles form responses; no SQL is in the route functions.
- `app/templates/index.html` is the server-rendered Jinja2 homepage with responsive inline CSS. No JavaScript or external assets are required.
- `app/templates/watchlist.html` provides mobile-friendly lists, labeled add forms, removal buttons, and error messages.
- `requirements.txt` lists FastAPI, Uvicorn (the server), Jinja2 (templates), and python-multipart (form parsing).
- `requirements-dev.txt` adds only httpx for FastAPI's test client. Tests use Python's built-in unittest runner.
- `tests/test_watchlist.py` contains regression and persistence tests with isolated temporary databases.
- `.env.example` documents that no configuration is currently required.
- `.gitignore` excludes environment files, virtual environments, Python artifacts, SQLite databases and sidecar files, and local data directories. Store future local data in `data/` or `local_data/`.
- `README.md` contains setup, run, and verification instructions.
