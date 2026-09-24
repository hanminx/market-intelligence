import unittest
from datetime import datetime, timezone

import httpx

from app.news.massive import MassiveNewsProvider, normalize_article
from app.news.provider import ProviderError


SINCE = datetime(2026, 9, 15, tzinfo=timezone.utc)


def raw_article(**changes):
    article = {
        "id": "article-1", "title": " A headline ", "description": " A summary ",
        "article_url": "https://example.com/news/1", "publisher": {"name": " Publisher "},
        "published_utc": "2026-09-21T09:30:00-04:00", "tickers": ["aapl", " MSFT ", "AAPL"],
    }
    article.update(changes)
    return article


class MassiveProviderTests(unittest.TestCase):
    def fetch(self, handler, max_pages=100):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return MassiveNewsProvider("test-key", client, max_pages=max_pages).fetch_news("AAPL", SINCE)

    def test_normalization_and_optional_summary(self):
        article = normalize_article(raw_article())
        self.assertEqual(article.provider, "massive")
        self.assertEqual(article.provider_article_id, "article-1")
        self.assertEqual(article.title, "A headline")
        self.assertEqual(article.summary, "A summary")
        self.assertEqual(article.publisher, "Publisher")
        self.assertEqual(article.tickers, ("AAPL", "MSFT"))
        self.assertEqual(article.published_at, datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc))
        without_description = raw_article()
        del without_description["description"]
        self.assertEqual(normalize_article(without_description).summary, "")
        self.assertEqual(normalize_article(raw_article(description=None)).summary, "")

    def test_request_filters_authentication_and_pagination(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertNotIn("apiKey", request.url.params)
            if len(requests) == 1:
                self.assertEqual(request.url.host, "api.massive.com")
                self.assertEqual(request.url.path, "/v2/reference/news")
                self.assertEqual(request.url.params["ticker"], "AAPL")
                self.assertEqual(request.url.params["published_utc.gte"], "2026-09-15T00:00:00.000000Z")
                self.assertEqual(request.url.params["order"], "desc")
                self.assertEqual(request.url.params["sort"], "published_utc")
                return httpx.Response(200, json={"status": "OK", "results": [raw_article()],
                    "next_url": "https://api.massive.com:443/v2/reference/news?cursor=next&apiKey=discard"})
            self.assertEqual(request.url.params["cursor"], "next")
            return httpx.Response(200, json={"results": [raw_article(id="article-2")]})
        articles = self.fetch(handler)
        self.assertEqual(len(requests), 2)
        self.assertEqual([article.provider_article_id for article in articles], ["article-1", "article-2"])

    def test_empty_results_and_date_boundary(self):
        for payload in ({"results": []}, {"status": "OK", "count": 0}, {"status": "OK"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.fetch(lambda request: httpx.Response(200, json=payload)), [])
        results = [raw_article(published_utc="2026-09-14T23:59:59Z"),
                   raw_article(id="boundary", published_utc="2026-09-15T00:00:00Z")]
        self.assertEqual([a.provider_article_id for a in self.fetch(
            lambda request: httpx.Response(200, json={"results": results}))], ["boundary"])

    def test_missing_key(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: self.fail("Unexpected request"))) as client:
            with self.assertRaisesRegex(ProviderError, "MASSIVE_API_KEY"):
                MassiveNewsProvider("  ", client)

    def test_http_errors_do_not_leak_response_or_key(self):
        for status, message in ((401, "rejected access"), (403, "rejected access"),
                                (429, "rate limit"), (500, "HTTP 500"), (302, "HTTP 302")):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ProviderError, message) as caught:
                    self.fetch(lambda request: httpx.Response(status, text="test-key secret provider error",
                               headers={"Location": "https://untrusted.example"}))
                self.assertNotIn("test-key", str(caught.exception))
                self.assertNotIn("secret provider error", str(caught.exception))

    def test_network_errors_are_readable_and_redacted(self):
        for error in (httpx.ConnectError, httpx.ReadTimeout):
            def handler(request):
                raise error("test-key secret request information", request=request)
            with self.subTest(error=error):
                with self.assertRaisesRegex(ProviderError, "network error or timeout") as caught:
                    self.fetch(handler)
                self.assertNotIn("test-key", str(caught.exception))

    def test_malformed_envelopes_and_json(self):
        for payload in ([], {}, {"results": {}}, {"results": None},
                        {"status": "ERROR", "results": []}, {"status": "OK", "count": 2}):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                self.fetch(lambda request: httpx.Response(200, json=payload))
        with self.assertRaisesRegex(ProviderError, "invalid JSON"):
            self.fetch(lambda request: httpx.Response(200, content=b"not-json test-key"))

    def test_malformed_articles(self):
        invalid = [None, [], {}, raw_article(id=""), raw_article(title=12),
                   raw_article(publisher=None), raw_article(publisher={}), raw_article(description=[]),
                   raw_article(published_utc="yesterday"), raw_article(published_utc="2026-09-21T12:00:00"),
                   raw_article(article_url="javascript:alert(1)"), raw_article(article_url="https:///missing-host"),
                   raw_article(tickers="AAPL"), raw_article(tickers=[]), raw_article(tickers=[12])]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaisesRegex(ProviderError, "malformed article"):
                normalize_article(payload)
        with self.assertRaisesRegex(ProviderError, "unrelated"):
            self.fetch(lambda request: httpx.Response(200, json={"results": [raw_article(tickers=["MSFT"])]}))

    def test_bad_pagination_never_sends_second_request(self):
        for url in ("https://evil.example/v2/reference/news", "http://api.massive.com/v2/reference/news",
                    "https://api.massive.com/other", "https://api.massive.com:444/v2/reference/news",
                    "https://user:password@api.massive.com/v2/reference/news", "", [], 12):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(200, json={"results": [], "next_url": url})
            with self.subTest(url=url), self.assertRaisesRegex(ProviderError, "pagination URL"):
                self.fetch(handler)
            self.assertEqual(len(requests), 1)

    def test_repeated_and_excessive_pagination_fail(self):
        def repeated(request):
            return httpx.Response(200, json={"results": [], "next_url": str(request.url)})
        with self.assertRaisesRegex(ProviderError, "repeated"):
            self.fetch(repeated)
        with self.assertRaisesRegex(ProviderError, "pagination exceeded"):
            self.fetch(lambda request: httpx.Response(200, json={"results": [],
                       "next_url": "https://api.massive.com/v2/reference/news?cursor=next"}), max_pages=1)


if __name__ == "__main__":
    unittest.main()
