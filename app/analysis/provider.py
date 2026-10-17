"""Replaceable scorer contract and provider-neutral result."""

from dataclasses import dataclass
from typing import Protocol

from app.analysis.config import ScoringConfig
from app.analysis.schema import ArticleAnalysis


class ScoringError(Exception):
    """Safe error text; must not include secrets, prompts, or raw API responses."""


@dataclass(frozen=True)
class ScoredArticle:
    analysis: ArticleAnalysis
    response_model: str
    response_id: str | None = None


class ArticleScorer(Protocol):
    provider: str
    config: ScoringConfig

    def score(self, input_json: str) -> ScoredArticle:
        """Analyze an exact serialized evidence snapshot or raise ScoringError."""
        ...
