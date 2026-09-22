# Market Intelligence

A minimal, mobile-first foundation for a personal US stock market research system.
Currently it serves a homepage and a health endpoint only. No AI services, data
APIs, databases, authentication, or background jobs are included.

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

The homepage should fit a mobile viewport without horizontal scrolling.

## Files

```text
app/
  __init__.py
  main.py
  templates/
    index.html
.env.example
.gitignore
requirements.txt
README.md
```

- `app/__init__.py` marks the application as a Python package.
- `app/main.py` creates FastAPI and defines `GET /` and `GET /health`.
- `app/templates/index.html` is the server-rendered Jinja2 homepage with responsive inline CSS. No JavaScript or external assets are required.
- `requirements.txt` lists FastAPI, Uvicorn (the server), and Jinja2 (templates).
- `.env.example` documents that no configuration is currently required.
- `.gitignore` excludes environment files, virtual environments, Python artifacts, SQLite databases and sidecar files, and local data directories. Store future local data in `data/` or `local_data/`.
- `README.md` contains setup, run, and verification instructions.
