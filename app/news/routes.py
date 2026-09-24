"""Read-only news pages. Viewing news never triggers provider requests."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.news.repository import list_articles

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
PAGE_SIZE = 50


@router.get("/news", response_class=HTMLResponse)
def news(request: Request, page: Annotated[int, Query(ge=1, le=1_000_000)] = 1):
    articles = list_articles(limit=PAGE_SIZE + 1, offset=(page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(
        request=request,
        name="news.html",
        context={"articles": articles[:PAGE_SIZE], "page": page, "has_next": len(articles) > PAGE_SIZE},
    )
