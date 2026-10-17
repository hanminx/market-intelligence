"""Manual article scoring: python -m app.analysis.scorer [--limit 20]."""

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field

from openai import OpenAI
from pydantic import ValidationError

from app import database
from app.analysis.config import canonical_json, current_config
from app.analysis.locking import scoring_lock
from app.analysis.openai_provider import OpenAIArticleScorer
from app.analysis.provider import ArticleScorer, ScoringError
from app.analysis.repository import count_scored, has_analysis, pending_articles, save_analysis
from app.analysis.schema import ArticleAnalysis


@dataclass
class ScoringSummary:
    attempted: int = 0
    scored: int = 0
    already_scored: int = 0
    surfaced: int = 0
    errors: list[str] = field(default_factory=list)


def score_articles(scorer: ArticleScorer, limit: int = 20) -> ScoringSummary:
    if limit < 1:
        raise ValueError("limit must be positive")
    with scoring_lock():
        return _score_pending(scorer, limit)


def _score_pending(scorer: ArticleScorer, limit: int) -> ScoringSummary:
    config = scorer.config
    summary = ScoringSummary(already_scored=count_scored(config))
    watched = [row["symbol"] for row in database.list_tickers()]
    for article in pending_articles(config, limit):
        if has_analysis(article["id"], config):
            summary.already_scored += 1
            continue
        # Snapshot is the exact user message sent to the model. No current-time
        # or prior-analysis context changes its article-level meaning.
        input_json = canonical_json({"article": article, "watched_tickers": watched})
        summary.attempted += 1
        try:
            result = scorer.score(input_json)
            analysis = ArticleAnalysis.model_validate(result.analysis.model_dump())
            if not set(analysis.primary_tickers).issubset(article["tickers"]):
                raise ScoringError("Analysis invented primary ticker associations; no analysis was saved.")
            if not watched and analysis.ticker_relevance_score != 0:
                raise ScoringError("Analysis claimed watchlist relevance for an empty watchlist.")
            if save_analysis(article["id"], result, config, scorer.provider, input_json):
                summary.scored += 1
                summary.surfaced += int(analysis.should_surface)
            else:
                summary.already_scored += 1
        except ScoringError as error:
            summary.errors.append(f"Article {article['id']}: {error}")
        except (ValidationError, TypeError, AttributeError, ValueError):
            summary.errors.append(f"Article {article['id']}: malformed analysis; nothing saved.")
        except sqlite3.Error:
            summary.errors.append(f"Article {article['id']}: could not save analysis to SQLite.")
    return summary


def print_summary(summary: ScoringSummary) -> None:
    print(f"Articles attempted: {summary.attempted}")
    print(f"scored: {summary.scored}")
    print(f"already scored: {summary.already_scored}")
    print(f"failed: {len(summary.errors)}")
    print(f"surfaced: {summary.surfaced}")
    for error in summary.errors:
        print(error, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score unanalysed articles for the current model/prompt version.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum pending articles to attempt (1–1000; default 20).")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    try:
        database.initialize()
        config = current_config()
        print(f"Model: {config.model}; prompt version: {config.prompt_version}")
        if not pending_articles(config, 1):
            print_summary(ScoringSummary(already_scored=count_scored(config)))
            print("No pending articles for this model/prompt version.")
            return 0
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ScoringError("Set OPENAI_API_KEY before scoring articles.")
        with OpenAI(api_key=api_key, timeout=30.0, max_retries=0) as client:
            summary = score_articles(OpenAIArticleScorer(client, config), args.limit)
        print_summary(summary)
        return 1 if summary.errors else 0
    except ScoringError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError):
        print("Scoring stopped: could not access local storage. Check data/ permissions and database availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
