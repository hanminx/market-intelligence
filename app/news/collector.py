"""Manual collection command: python -m app.news.collector [--days 7]."""

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from app import database
from app.news.massive import MassiveNewsProvider
from app.news.provider import NewsProvider, ProviderError
from app.news.repository import save_articles


@dataclass
class CollectionResult:
    tickers: int = 0
    fetched: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)


def collect_watchlist_news(provider: NewsProvider, since: datetime) -> CollectionResult:
    """Keep successful tickers even when another ticker's fetch fails."""
    result = CollectionResult()
    for row in database.list_tickers():
        ticker = row["symbol"]
        result.tickers += 1
        try:
            articles = provider.fetch_news(ticker, since)
        except ProviderError as error:
            result.errors.append(f"{ticker}: {error}")
            continue
        result.inserted += save_articles(articles)
        result.fetched += len(articles)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect recent news for watched tickers.")
    parser.add_argument("--days", type=int, default=7, help="Look back 1–365 days (default: 7).")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 365:
        parser.error("--days must be between 1 and 365")

    try:
        database.initialize()
        if not database.list_tickers():
            print("No watched tickers. Add a ticker at /watchlist, then run this command again.")
            return 0
        api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
        if not api_key:
            raise ProviderError("Set MASSIVE_API_KEY before collecting news.")
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        with httpx.Client() as client:
            provider = MassiveNewsProvider(api_key, client)
            result = collect_watchlist_news(provider, since)
        print(
            f"Tickers attempted: {result.tickers}; articles fetched: {result.fetched}; "
            f"new articles: {result.inserted}; already stored: {result.fetched - result.inserted}; "
            f"failed tickers: {len(result.errors)}."
        )
        for error in result.errors:
            print(error, file=sys.stderr)
        return 1 if result.errors else 0
    except ProviderError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError):
        print("Collection stopped: could not access local storage. Check data/ permissions and database availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
