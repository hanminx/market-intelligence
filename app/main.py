from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import database
from app.watchlist import router as watchlist_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    yield


app = FastAPI(title="Market Intelligence", lifespan=lifespan)
app.include_router(watchlist_router)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
