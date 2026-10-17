"""Version-aware selection and immutable analysis writes."""

import hashlib
import json
from datetime import datetime, timezone

from app.database import connect
from app.analysis.config import ScoringConfig, canonical_json
from app.analysis.provider import ScoredArticle
from app.analysis.schema import ArticleAnalysis
from app.news.provider import utc_timestamp
from app.news.repository import attach_tickers


def count_scored(config: ScoringConfig) -> int:
    with connect() as connection:
        return connection.execute(
            "SELECT count(*) FROM article_analyses WHERE model = ? AND prompt_version = ?",
            (config.model, config.prompt_version),
        ).fetchone()[0]


def pending_articles(config: ScoringConfig, limit: int) -> list[dict]:
    with connect() as connection:
        rows = connection.execute("""
            SELECT n.* FROM news_articles n
            WHERE NOT EXISTS (
                SELECT 1 FROM article_analyses a
                WHERE a.article_id = n.id AND a.model = ? AND a.prompt_version = ?
            ) ORDER BY n.published_at DESC, n.id DESC LIMIT ?
        """, (config.model, config.prompt_version, limit)).fetchall()
        return attach_tickers(connection, [dict(row) for row in rows])


def has_analysis(article_id: int, config: ScoringConfig) -> bool:
    with connect() as connection:
        return connection.execute(
            "SELECT 1 FROM article_analyses WHERE article_id = ? AND model = ? AND prompt_version = ?",
            (article_id, config.model, config.prompt_version),
        ).fetchone() is not None


def save_analysis(article_id: int, result: ScoredArticle, config: ScoringConfig,
                  provider: str, input_json: str) -> bool:
    analysis = ArticleAnalysis.model_validate(result.analysis.model_dump())
    values = analysis.model_dump()
    values["primary_tickers"] = canonical_json(analysis.primary_tickers)
    values.update(
        article_id=article_id, provider=provider, model=config.model,
        prompt_version=config.prompt_version,
        scored_at=utc_timestamp(datetime.now(timezone.utc)),
        input_json=input_json, input_sha256=hashlib.sha256(input_json.encode()).hexdigest(),
        prompt_text=config.prompt, schema_json=config.schema_json,
        request_settings_json=config.settings_json, response_model=result.response_model,
        response_id=result.response_id,
    )
    # Column names come only from the strict internal schema and fixed metadata above.
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with connect() as connection:
        inserted = connection.execute(
            f"INSERT INTO article_analyses ({columns}) VALUES ({placeholders}) "
            "ON CONFLICT(article_id, model, prompt_version) DO NOTHING",
            tuple(values.values()),
        )
        return inserted.rowcount == 1


def list_news(config: ScoringConfig, surfaced_only: bool = False,
              limit: int = 50, offset: int = 0) -> list[dict]:
    fields = [*ArticleAnalysis.model_fields, "model", "prompt_version", "scored_at"]
    selected = ", ".join(f"a.{field} AS ai_{field}" for field in fields)
    with connect() as connection:
        rows = connection.execute(f"""
            SELECT n.*, a.id AS analysis_id, {selected}
            FROM news_articles n
            LEFT JOIN article_analyses a
                ON a.article_id = n.id AND a.model = ? AND a.prompt_version = ?
            WHERE (? = 0 OR a.should_surface = 1)
            ORDER BY n.published_at DESC, n.id DESC LIMIT ? OFFSET ?
        """, (config.model, config.prompt_version, surfaced_only, limit, offset)).fetchall()
        articles = []
        for row in rows:
            article = dict(row)
            analysis = {field: article.pop(f"ai_{field}") for field in fields}
            if article.pop("analysis_id") is not None:
                analysis["primary_tickers"] = json.loads(analysis["primary_tickers"])
                article["analysis"] = analysis
            else:
                article["analysis"] = None
            articles.append(article)
        return attach_tickers(connection, articles)
