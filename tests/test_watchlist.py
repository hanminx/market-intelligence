import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database
from app.main import app


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "data" / "intelligence.db"
        patched_path = patch.object(database, "DATABASE_PATH", self.path)
        patched_path.start()
        self.addCleanup(patched_path.stop)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.client = stack.enter_context(TestClient(app))

    def test_home_health_and_empty_watchlist(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Market Intelligence", home.text)
        self.assertIn("System is running", home.text)
        self.assertIn('href="/watchlist"', home.text)
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        page = self.client.get("/watchlist")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("No stocks watched yet", page.text)
        self.assertIn("No sectors watched yet", page.text)

    def test_add_normalization_duplicates_and_removal(self):
        for resource, field, value, normalized, duplicate, read in (
            ("tickers", "symbol", "  aapl \t", "AAPL", "aApL", database.list_tickers),
            ("sectors", "name", "  Technology \t", "Technology", "technology", database.list_sectors),
        ):
            with self.subTest(resource=resource):
                endpoint = f"/watchlist/{resource}"
                added = self.client.post(endpoint, data={field: value}, follow_redirects=False)
                self.assertEqual(added.status_code, 303)
                self.assertEqual(added.headers["location"], "/watchlist")
                rows = read()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][field], normalized)
                self.assertTrue(rows[0]["created_at"])
                self.assertIn(normalized, self.client.get("/watchlist").text)
                rejected = self.client.post(endpoint, data={field: duplicate})
                self.assertEqual(rejected.status_code, 400)
                self.assertIn("already on your watchlist", rejected.text)
                self.assertEqual(len(read()), 1)
                remove_url = f"{endpoint}/{rows[0]['id']}/remove"
                self.assertEqual(self.client.get(remove_url).status_code, 405)
                self.assertEqual(len(read()), 1)
                removed = self.client.post(remove_url, follow_redirects=False)
                self.assertEqual(removed.status_code, 303)
                self.assertEqual(read(), [])
                # Repeating removal of a missing row is harmless.
                self.assertEqual(self.client.post(remove_url).status_code, 200)

    def test_empty_and_missing_values(self):
        for resource, field, read in (
            ("tickers", "symbol", database.list_tickers),
            ("sectors", "name", database.list_sectors),
        ):
            for payload in ({}, {field: ""}, {field: " \t\n "}):
                with self.subTest(resource=resource, payload=payload):
                    response = self.client.post(f"/watchlist/{resource}", data=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('role="alert"', response.text)
                    self.assertEqual(read(), [])

    def test_persistence_on_application_restart(self):
        with TestClient(app) as first:
            first.post("/watchlist/tickers", data={"symbol": "msft"})
            first.post("/watchlist/sectors", data={"name": "Energy"})
        with TestClient(app) as restarted:
            page = restarted.get("/watchlist")
            self.assertIn("MSFT", page.text)
            self.assertIn("Energy", page.text)
        # An independent SQLite connection sees committed data on disk.
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT symbol FROM watched_tickers").fetchone()[0], "MSFT")
            self.assertEqual(connection.execute("SELECT name FROM watched_sectors").fetchone()[0], "Energy")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO watched_tickers (symbol) VALUES ('MSFT')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO watched_sectors (name) VALUES ('energy')")
        finally:
            connection.close()

    def test_user_values_are_sql_parameters_and_html_escaped(self):
        value = "<script>alert('test')</script>"
        response = self.client.post("/watchlist/sectors", data={"name": value})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(value, response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertEqual(database.list_sectors()[0]["name"], value)
        duplicate = self.client.post("/watchlist/sectors", data={"name": value})
        self.assertEqual(duplicate.status_code, 400)
        self.assertNotIn(value, duplicate.text)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
