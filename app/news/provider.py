"""Vendor-independent contract used by the collector and storage layer."""
# 一套“新闻数据的统一标准 + 新闻供应商必须遵守的接口”。
# 这样以后不管底层用 Massive、Polygon、Finnhub，
# collector 和 storage 都不需要跟着改。

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class Article:
    provider: str
    provider_article_id: str
    title: str
    summary: str
    url: str
    publisher: str
    published_at: datetime
    tickers: tuple[str, ...]


class ProviderError(Exception):
    """A safe, user-facing provider failure; never include keys or raw responses."""


class NewsProvider(Protocol):
    def fetch_news(self, ticker: str, since: datetime) -> list[Article]:
        """Return normalized articles since an aware timestamp or raise ProviderError.

        Articles must have an aware publication time, an HTTP(S) source URL,
        nonempty identifiers/title/publisher, and uppercase ticker associations.
        A missing provider description is represented by an empty summary.
        """
        ...


def utc_timestamp(value: datetime) -> str:
    """ Use one fixed-width UTC representation for reliable SQLite text sorting. """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
