"""Article persistence, independent of the provider implementation."""

from datetime import datetime, timezone

from app.database import connect
from app.news.provider import Article, utc_timestamp


def save_articles(articles: list[Article]) -> int:
    """Commit one ticker's batch atomically; return the number of new articles.

    Repeated articles retain their original content and first fetch time, while
    any newly discovered ticker associations are merged into the existing row.
    """
    fetched_at = utc_timestamp(datetime.now(timezone.utc))
    inserted = 0
    with connect() as connection:
        for article in articles:
            result = connection.execute("""
                INSERT INTO news_articles (
                    provider, provider_article_id, title, summary, url,
                    publisher, published_at, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_article_id) DO NOTHING
            """, (
                article.provider, article.provider_article_id, article.title,
                article.summary, article.url, article.publisher,
                utc_timestamp(article.published_at), fetched_at,
            ))
            inserted += result.rowcount
            article_id = connection.execute(
                "SELECT id FROM news_articles WHERE provider = ? AND provider_article_id = ?",
                (article.provider, article.provider_article_id),
            ).fetchone()["id"]
            connection.executemany("""
                INSERT INTO news_article_tickers (article_id, symbol) VALUES (?, ?)
                ON CONFLICT(article_id, symbol) DO NOTHING
            """, [(article_id, ticker) for ticker in article.tickers])
    return inserted


def list_articles(limit: int = 50, offset: int = 0) -> list[dict]:
    with connect() as connection:
        articles = [dict(row) for row in connection.execute("""
            SELECT * FROM news_articles
            ORDER BY published_at DESC, id DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()]
        return attach_tickers(connection, articles)


def attach_tickers(connection, articles: list[dict]) -> list[dict]:
    """Load source ticker associations in one query for an article page."""
    if not articles:
        return []
    by_id = {article["id"]: article for article in articles}
    for article in articles:
        article["tickers"] = []
    placeholders = ",".join("?" for _ in articles)
    rows = connection.execute(
        f"SELECT article_id, symbol FROM news_article_tickers "
        f"WHERE article_id IN ({placeholders}) ORDER BY symbol",
        tuple(by_id),
    ).fetchall()
    for row in rows:
        by_id[row["article_id"]]["tickers"].append(row["symbol"])
    return articles
