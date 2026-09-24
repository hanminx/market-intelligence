"""Shared SQLite setup and watchlist persistence."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "intelligence.db"


@contextmanager
def connect():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS watched_tickers (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE
                    CHECK (length(trim(symbol)) > 0 AND symbol = upper(symbol)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS watched_sectors (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
                    CHECK (length(trim(name)) > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_article_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                publisher TEXT NOT NULL,
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE (provider, provider_article_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS news_article_tickers (
                article_id INTEGER NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                PRIMARY KEY (article_id, symbol)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS news_articles_published
            ON news_articles (published_at DESC, id DESC)
        """)


def list_tickers() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            "SELECT id, symbol, created_at FROM watched_tickers ORDER BY symbol"
        ).fetchall()


def add_ticker(symbol: str) -> None:
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Enter a ticker symbol.")
    with connect() as connection:
        result = connection.execute(
            "INSERT INTO watched_tickers (symbol) VALUES (?) "
            "ON CONFLICT(symbol) DO NOTHING",
            (symbol,),
        )
        if result.rowcount == 0:
            raise ValueError(f"{symbol} is already on your watchlist.")


def remove_ticker(ticker_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM watched_tickers WHERE id = ?", (ticker_id,))


def list_sectors() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            "SELECT id, name, created_at FROM watched_sectors ORDER BY name"
        ).fetchall()


def add_sector(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Enter a sector name.")
    with connect() as connection:
        result = connection.execute(
            "INSERT INTO watched_sectors (name) VALUES (?) "
            "ON CONFLICT(name) DO NOTHING",
            (name,),
        )
        if result.rowcount == 0:
            raise ValueError(f"{name} is already on your watchlist.")


def remove_sector(sector_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM watched_sectors WHERE id = ?", (sector_id,))
