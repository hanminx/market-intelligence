"""Server-rendered watchlist page and form handlers."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import database

router = APIRouter(prefix="/watchlist")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def render_watchlist(
    request: Request, error: str | None = None, symbol: str = "", name: str = ""
):
    return templates.TemplateResponse(
        request=request,
        name="watchlist.html",
        context={
            "tickers": database.list_tickers(),
            "sectors": database.list_sectors(),
            "error": error,
            "symbol": symbol,
            "name": name,
        },
        status_code=400 if error else 200,
    )


@router.get("", response_class=HTMLResponse)
def watchlist(request: Request):
    return render_watchlist(request)


@router.post("/tickers", response_class=HTMLResponse)
def add_ticker(request: Request, symbol: Annotated[str, Form()] = ""):
    try:
        database.add_ticker(symbol)
    except ValueError as error:
        return render_watchlist(request, error=str(error), symbol=symbol)
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/tickers/{ticker_id}/remove")
def remove_ticker(ticker_id: int):
    database.remove_ticker(ticker_id)
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/sectors", response_class=HTMLResponse)
def add_sector(request: Request, name: Annotated[str, Form()] = ""):
    try:
        database.add_sector(name)
    except ValueError as error:
        return render_watchlist(request, error=str(error), name=name)
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/sectors/{sector_id}/remove")
def remove_sector(sector_id: int):
    database.remove_sector(sector_id)
    return RedirectResponse(url="/watchlist", status_code=303)
