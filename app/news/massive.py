"""Massive Stocks News REST adapter. No database or web-app dependencies."""

from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.news.provider import Article, ProviderError, utc_timestamp

ENDPOINT = "https://api.massive.com/v2/reference/news"


def required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text.")
    return value.strip()


def normalize_article(raw: object) -> Article:
    try:
        if not isinstance(raw, dict):
            raise ValueError("Expected an article object.")
        published = datetime.fromisoformat(required_text(raw["published_utc"]).replace("Z", "+00:00"))
        # Validate timezone and normalize to UTC, including on Python 3.10.
        published = datetime.fromisoformat(utc_timestamp(published).replace("Z", "+00:00"))
        url = required_text(raw["article_url"])
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Expected an HTTP(S) source URL.")
        tickers = raw["tickers"]
        if not isinstance(tickers, list) or not tickers:
            raise ValueError("Expected ticker associations.")
        normalized_tickers = tuple(sorted({required_text(ticker).upper() for ticker in tickers}))
        summary = raw.get("description")
        if summary is None:
            summary = ""
        if not isinstance(summary, str):
            raise ValueError("Expected description text.")
        return Article(
            provider="massive",
            provider_article_id=required_text(raw["id"]),
            title=required_text(raw["title"]),
            summary=summary.strip(),
            url=url,
            publisher=required_text(raw["publisher"]["name"]),
            published_at=published,
            tickers=normalized_tickers,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ProviderError("Massive returned a malformed article (check required fields, timestamp, and source URL).") from None


class MassiveNewsProvider:
    def __init__(self, api_key: str, client: httpx.Client, max_pages: int = 100):
        if not api_key.strip():
            raise ProviderError("Set MASSIVE_API_KEY before collecting news.")
        if max_pages < 1:
            raise ValueError("max_pages must be positive.")
        self._api_key = api_key.strip()
        self._client = client
        self._max_pages = max_pages

    def fetch_news(self, ticker: str, since: datetime) -> list[Article]:
        url = httpx.URL(ENDPOINT, params={
            "ticker": ticker,
            "published_utc.gte": utc_timestamp(since),
            "sort": "published_utc",
            "order": "desc",
            "limit": 100,
        })
        articles = []
        visited = set()
        for _ in range(self._max_pages):
            if str(url) in visited:
                raise ProviderError("Massive returned a repeated pagination URL.")
            visited.add(str(url))
            payload = self._get_page(url)
            if not isinstance(payload, dict):
                raise ProviderError("Massive returned a malformed response object.")
            if payload.get("status") not in (None, "OK"):
                raise ProviderError("Massive reported an unsuccessful response. Check API access and try again.")
            results = payload.get("results")
            # Massive may omit its optional results field for a successful empty query.
            if "results" not in payload and payload.get("status") == "OK" and payload.get("count", 0) == 0:
                results = []
            if not isinstance(results, list):
                raise ProviderError("Massive returned malformed results; expected an article list.")
            for raw in results:
                article = normalize_article(raw)
                if ticker not in article.tickers:
                    raise ProviderError("Massive returned an article unrelated to the requested ticker.")
                if article.published_at >= since:
                    articles.append(article)
            next_url = payload.get("next_url")
            if next_url is None:
                return articles
            url = self._pagination_url(next_url)
        raise ProviderError(f"Massive pagination exceeded {self._max_pages} pages; collect a smaller date range.")

    def _get_page(self, url: httpx.URL) -> object:
        try:
            response = self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=20.0,
                follow_redirects=False,
            )
            if response.status_code in (401, 403):
                raise ProviderError("Massive rejected access. Check MASSIVE_API_KEY and your plan permissions.")
            if response.status_code == 429:
                raise ProviderError("Massive rate limit reached. Wait and rerun the collector.")
            if response.status_code != 200:
                raise ProviderError(f"Massive request failed (HTTP {response.status_code}). Try again later.")
            return response.json()
        except httpx.RequestError:
            raise ProviderError("Could not reach Massive (network error or timeout). Try again later.") from None
        except (ValueError, UnicodeError):
            raise ProviderError("Massive returned invalid JSON.") from None

    @staticmethod
    def _pagination_url(value: object) -> httpx.URL:
        # Never forward credentials to another host, endpoint, or plaintext URL.
        try:
            url = httpx.URL(required_text(value))
            if (url.scheme != "https" or url.host != "api.massive.com"
                    or url.port not in (None, 443) or url.path != "/v2/reference/news"
                    or url.userinfo or url.fragment):
                raise ValueError("Unexpected pagination destination.")
            return url.copy_remove_param("apiKey")
        except (ValueError, httpx.InvalidURL):
            raise ProviderError("Massive returned an invalid pagination URL.") from None
