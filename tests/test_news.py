import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.news.collector import collect_watchlist_news, main
from app.news.massive import MassiveNewsProvider
from app.news.provider import Article, ProviderError
from app.news.repository import list_articles, save_articles


SINCE = datetime(2026, 9, 15, tzinfo=timezone.utc)


def article(**changes):
    return replace(Article(
        provider="fake", provider_article_id="one", title="A headline", summary="A summary",
        url="https://example.com/news/1", publisher="Publisher",
        published_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc), tickers=("AAPL", "MSFT"),
    ), **changes)


class FakeProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_news(self, ticker, since):
        self.calls.append((ticker, since))
        response = self.responses[ticker]
        if isinstance(response, Exception):
            raise response
        return response


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(temporary) / "data" / "intelligence.db"
        self.stack.enter_context(patch.object(database, "DATABASE_PATH", self.path))
        self.client = self.stack.enter_context(TestClient(app))

    def test_collects_only_watched_tickers_and_deduplicates(self):
        database.add_ticker("aapl")
        database.add_ticker("msft")
        database.add_sector("Energy")
        provider = FakeProvider({"AAPL": [article()], "MSFT": [article()]})
        result = collect_watchlist_news(provider, SINCE)
        self.assertEqual(provider.calls, [("AAPL", SINCE), ("MSFT", SINCE)])
        self.assertEqual((result.tickers, result.fetched, result.inserted, result.errors), (2, 2, 1, []))
        before = list_articles()
        self.assertEqual(before[0]["tickers"], ["AAPL", "MSFT"])
        self.assertEqual(collect_watchlist_news(provider, SINCE).inserted, 0)
        self.assertEqual(list_articles(), before)

    def test_new_associations_merge_and_provider_ids_are_scoped(self):
        self.assertEqual(save_articles([article(tickers=("AAPL",))]), 1)
        self.assertEqual(save_articles([article(tickers=("MSFT", "AAPL"))]), 0)
        self.assertEqual(list_articles()[0]["tickers"], ["AAPL", "MSFT"])
        self.assertEqual(save_articles([article(provider="another-vendor")]), 1)
        self.assertEqual(len(list_articles()), 2)
        with database.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO news_article_tickers VALUES (99999, 'AAPL')")

    def test_news_survives_watchlist_removal_and_restart(self):
        database.add_ticker("aapl")
        save_articles([article()])
        database.remove_ticker(database.list_tickers()[0]["id"])
        database.initialize()
        with TestClient(app) as restarted:
            self.assertIn("A headline", restarted.get("/news").text)
        self.assertEqual(list_articles()[0]["tickers"], ["AAPL", "MSFT"])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM news_articles").fetchone()[0], 1)

    def test_existing_watchlist_database_upgrade_is_additive(self):
        old_path = self.path.parent / "old.db"
        with closing(sqlite3.connect(old_path)) as connection, connection:
            connection.execute("CREATE TABLE watched_tickers (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE, created_at TEXT)")
            connection.execute("INSERT INTO watched_tickers VALUES (1, 'AAPL', '2026-09-01 12:00:00')")
            connection.execute("CREATE TABLE watched_sectors (id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT)")
            connection.execute("INSERT INTO watched_sectors VALUES (1, 'Energy', '2026-09-01 12:00:00')")
        with patch.object(database, "DATABASE_PATH", old_path):
            database.initialize()
            database.initialize()
            self.assertEqual(database.list_tickers()[0]["symbol"], "AAPL")
            self.assertEqual(database.list_sectors()[0]["name"], "Energy")
            self.assertEqual(save_articles([article()]), 1)

    def test_partial_provider_failure_keeps_successful_tickers(self):
        database.add_ticker("aapl")
        database.add_ticker("msft")
        result = collect_watchlist_news(FakeProvider({
            "AAPL": ProviderError("Network failure"), "MSFT": [article()],
        }), SINCE)
        self.assertEqual(result.errors, ["AAPL: Network failure"])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(len(list_articles()), 1)

    def test_malformed_later_page_saves_nothing_for_that_ticker(self):
        database.add_ticker("aapl")
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json={"results": [{
                    "id": "one", "title": "Title", "publisher": {"name": "Publisher"},
                    "article_url": "https://example.com", "tickers": ["AAPL"],
                    "published_utc": "2026-09-21T12:00:00Z",
                }], "next_url": "https://api.massive.com/v2/reference/news?cursor=next"})
            return httpx.Response(200, json={"results": [{"id": "broken"}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = collect_watchlist_news(MassiveNewsProvider("test-key", client), SINCE)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(list_articles(), [])

    def test_repository_batch_rolls_back_on_failure(self):
        with self.assertRaises(ValueError):
            save_articles([article(), article(provider_article_id="invalid", published_at=datetime(2026, 9, 21))])
        self.assertEqual(list_articles(), [])

    def test_news_page_content_sorting_escaping_and_navigation(self):
        save_articles([
            article(provider_article_id="older", title="Older headline", published_at=SINCE),
            article(title="Newest <script>headline</script>", summary="<b>Summary</b>"),
        ])
        with patch.object(MassiveNewsProvider, "fetch_news", side_effect=AssertionError("Web must not fetch")):
            response = self.client.get("/news")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        for text in ("2026-09-21 12:00:00 UTC", "AAPL", "MSFT", "Publisher", "https://example.com/news/1",
                     "&lt;script&gt;headline&lt;/script&gt;", "&lt;b&gt;Summary&lt;/b&gt;", 'name="viewport"'):
            self.assertIn(text, response.text)
        self.assertNotIn("<script>", response.text)
        self.assertLess(response.text.index("Newest"), response.text.index("Older headline"))
        for route in ("/", "/watchlist"):
            self.assertIn('href="/news"', self.client.get(route).text)

    def test_news_empty_missing_summary_and_pagination(self):
        self.assertIn("No news collected yet", self.client.get("/news").text)
        save_articles([article(provider_article_id=str(i), title=f"Headline {i:02}", summary="") for i in range(51)])
        first = self.client.get("/news")
        second = self.client.get("/news?page=2")
        self.assertIn("No summary provided", first.text)
        self.assertEqual(first.text.count("<article>"), 50)
        self.assertEqual(second.text.count("<article>"), 1)
        self.assertIn('href="/news?page=2"', first.text)
        self.assertIn('href="/news?page=1"', second.text)
        self.assertNotIn('href="/news?page=3"', second.text)
        self.assertIn("Headline 00", second.text)
        self.assertNotIn("Headline 00", first.text)
        self.assertEqual(self.client.get("/news?page=0").status_code, 422)

    def run_command(self, argv=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv or [])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_empty_watchlist_and_missing_key_do_not_request(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}), patch("app.news.collector.httpx.Client") as client:
            code, stdout, stderr = self.run_command()
            self.assertEqual(code, 0)
            self.assertIn("No watched tickers", stdout)
            database.add_ticker("aapl")
            code, stdout, stderr = self.run_command()
            self.assertEqual(code, 1)
            self.assertIn("MASSIVE_API_KEY", stderr)
            client.assert_not_called()

    def test_cli_success_failure_and_environment_loading(self):
        database.add_ticker("aapl")
        provider = FakeProvider({"AAPL": [article()]})
        with patch.dict(os.environ, {"MASSIVE_API_KEY": " test-key "}), \
                patch("app.news.collector.MassiveNewsProvider", return_value=provider) as factory:
            before = datetime.now(timezone.utc)
            code, stdout, stderr = self.run_command(["--days", "3"])
            self.assertEqual(code, 0)
            self.assertIn("new articles: 1", stdout)
            self.assertEqual(stderr, "")
            self.assertEqual(factory.call_args.args[0], "test-key")
            self.assertAlmostEqual((before - provider.calls[0][1]).total_seconds(), 3 * 86400, delta=5)
            provider.responses["AAPL"] = ProviderError("A safe failure")
            code, stdout, stderr = self.run_command()
            self.assertEqual(code, 1)
            self.assertIn("AAPL: A safe failure", stderr)
            self.assertNotIn("test-key", stdout + stderr)

    def test_cli_rejects_invalid_days(self):
        for days in ("0", "-1", "366", "abc"):
            with self.subTest(days=days), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(["--days", days])
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
