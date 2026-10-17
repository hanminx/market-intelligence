"""Typed Structured Output contract and local validation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Score = Annotated[int, Field(ge=0, le=100)]
ContentType = Literal[
    "company_event", "sector_event", "macro", "analyst", "opinion",
    "market_recap", "evergreen", "promotional", "other",
]


class ArticleAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    primary_tickers: list[str]
    ticker_relevance_score: Score
    content_type: ContentType
    novelty_score: Score
    fundamental_impact_score: Score
    market_impact_score: Score
    importance_score: Score
    impact_direction: Literal["positive", "negative", "mixed", "neutral", "unclear"]
    time_horizon: Literal["immediate", "short", "medium", "long"]
    is_opinion: bool
    should_surface: bool
    why_it_matters: Annotated[str, Field(min_length=1, max_length=600)]
    confidence_score: Score

    @field_validator("primary_tickers")
    @classmethod
    def validate_tickers(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip().upper() for value in values):
            raise ValueError("Primary tickers must be nonempty, trimmed and uppercase.")
        if len(values) != len(set(values)):
            raise ValueError("Primary tickers must be unique.")
        return values

    @field_validator("why_it_matters")
    @classmethod
    def validate_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An explanation is required.")
        return value

    @model_validator(mode="after")
    def validate_surfacing(self):
        eligible = (
            self.importance_score >= 60 and self.novelty_score >= 50
            and self.ticker_relevance_score >= 60 and self.confidence_score >= 60
            and not self.is_opinion
            and self.content_type in {"company_event", "sector_event", "macro", "analyst"}
        )
        if self.should_surface != eligible:
            raise ValueError("should_surface must match the versioned surfacing policy.")
        return self
