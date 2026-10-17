import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import database
from app.analysis.config import ScoringConfig, current_config
from app.analysis.locking import scoring_lock
from app.analysis.provider import ScoredArticle, ScoringError
from app.analysis.repository import count_scored, pending_articles, save_analysis
from app.analysis.schema import ArticleAnalysis
from app.analysis.scorer import main, score_articles
from app.main import app
from app.news.provider import Article
from app.news.repository import list_articles, save_articles


def judgment(**changes):
    data = dict(
        primary_tickers=["AAPL"], ticker_relevance_score=90, content_type="company_event",
        novelty_score=80, fundamental_impact_score=80, market_impact_score=70,
        importance_score=80, impact_direction="negative", time_horizon="short",
        is_opinion=False, should_surface=True,
        why_it_matters="The reported guidance cut reduces expected earnings.", confidence_score=85,
    )
    data.update(changes)
    return ArticleAnalysis.model_validate(data)


def source(identifier="one", **changes):
    article = Article(
        provider="fake", provider_article_id=identifier, title=f"Source {identifier}",
        summary="Company cuts guidance after losing a major customer.",
        url="https://example.com/source", publisher="Example",
        published_at=datetime(2026, 9, 21, tzinfo=timezone.utc), tickers=("AAPL", "MSFT"),
    )
    return replace(article, **changes)


class FakeScorer:
    provider = "fake"

    def __init__(self, responses=None, config=None):
        self.config = config or ScoringConfig()
        self.responses = responses or {}
        self.inputs = []

    def score(self, input_json):
        self.inputs.append(input_json)
        identifier = json.loads(input_json)["article"]["provider_article_id"]
        analysis = self.responses.get(identifier, judgment())
        if isinstance(analysis, Exception):
            raise analysis
        return ScoredArticle(analysis, self.config.model, "fake-response")


class SchemaTests(unittest.TestCase):
    def test_strict_scores_enums_booleans_and_required_fields(self):
        for change in (
            {"importance_score": -1}, {"importance_score": 101}, {"novelty_score": 50.5},
            {"confidence_score": "85"}, {"market_impact_score": True}, {"content_type": "unknown"},
            {"impact_direction": "buy"}, {"time_horizon": "forever"}, {"is_opinion": "false"},
            {"unexpected": 12}, {"why_it_matters": " "}, {"primary_tickers": ["aapl"]},
            {"primary_tickers": ["AAPL", "AAPL"]}, {"should_surface": False},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                judgment(**change)
        data = judgment().model_dump()
        del data["novelty_score"]
        with self.assertRaises(ValidationError):
            ArticleAnalysis.model_validate(data)

    def test_surface_policy_thresholds_and_exclusions(self):
        for changes in (
            {"importance_score": 59}, {"novelty_score": 49}, {"ticker_relevance_score": 59},
            {"confidence_score": 59}, {"is_opinion": True}, {"content_type": "market_recap"},
            {"content_type": "evergreen"}, {"content_type": "promotional"},
            {"content_type": "opinion"}, {"content_type": "other"},
        ):
            self.assertFalse(judgment(should_surface=False, **changes).should_surface)
        self.assertTrue(judgment(importance_score=60, novelty_score=50,
                                 ticker_relevance_score=60, confidence_score=60).should_surface)

    def test_version_changes_with_prompt_and_schema_and_settings(self):
        config = ScoringConfig()
        self.assertEqual(config.prompt_version, ScoringConfig().prompt_version)
        self.assertNotEqual(config.prompt_version, replace(config, prompt=config.prompt + "\nNew rubric.").prompt_version)
        with patch("app.analysis.config.REQUEST_SETTINGS", {"max_output_tokens": 1000}):
            changed_version = config.prompt_version
        self.assertNotEqual(changed_version, config.prompt_version)
        with patch.object(ArticleAnalysis, "model_json_schema", return_value={"type": "object"}):
            changed_version = config.prompt_version
        self.assertNotEqual(changed_version, config.prompt_version)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temporary = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(database, "DATABASE_PATH", Path(temporary) / "data" / "intelligence.db"))
        stack.enter_context(patch.dict(os.environ, {"OPENAI_MODEL": "", "OPENAI_API_KEY": ""}))
        self.client = stack.enter_context(TestClient(app))
        database.add_ticker("AAPL")

    def test_immutable_raw_data_audit_snapshot_and_idempotency(self):
        save_articles([source()])
        raw_before = list_articles()
        scorer = FakeScorer()
        summary = score_articles(scorer)
        self.assertEqual((summary.attempted, summary.scored, summary.surfaced, summary.errors), (1, 1, 1, []))
        with database.connect() as connection:
            row = dict(connection.execute("SELECT * FROM article_analyses").fetchone())
        self.assertEqual(row["input_json"], scorer.inputs[0])
        self.assertEqual(row["input_sha256"], hashlib.sha256(scorer.inputs[0].encode()).hexdigest())
        self.assertEqual(json.loads(row["input_json"])["watched_tickers"], ["AAPL"])
        self.assertEqual(json.loads(row["primary_tickers"]), ["AAPL"])
        self.assertEqual(row["prompt_text"], scorer.config.prompt)
        self.assertEqual(row["schema_json"], scorer.config.schema_json)
        self.assertEqual(row["request_settings_json"], scorer.config.settings_json)
        self.assertTrue(row["scored_at"].endswith("Z"))
        self.assertEqual(row["response_id"], "fake-response")
        self.assertEqual(list_articles(), raw_before)
        database.add_ticker("MSFT")
        rerun = score_articles(scorer)
        self.assertEqual((rerun.attempted, rerun.already_scored), (0, 1))
        self.assertEqual(len(scorer.inputs), 1)
        with database.connect() as connection:
            self.assertEqual(dict(connection.execute("SELECT * FROM article_analyses").fetchone()), row)

    def test_database_uniqueness_versions_and_restart(self):
        save_articles([source()])
        scorer = FakeScorer()
        score_articles(scorer)
        article_id = list_articles()[0]["id"]
        self.assertFalse(save_analysis(article_id, ScoredArticle(judgment(), scorer.config.model),
                                       scorer.config, scorer.provider, scorer.inputs[0]))
        new_model = FakeScorer(config=replace(scorer.config, model="another-snapshot"))
        new_prompt = FakeScorer(config=replace(scorer.config, prompt=scorer.config.prompt + "\nRevised rubric."))
        self.assertEqual(score_articles(new_model).scored, 1)
        self.assertEqual(score_articles(new_prompt).scored, 1)
        database.initialize()
        self.assertEqual(count_scored(scorer.config), 1)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM article_analyses").fetchone()[0], 3)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE article_analyses SET article_id = 9999")

    def test_partial_failure_retains_successes_and_rerun_only_retries_failure(self):
        save_articles([source("third"), source("failure"), source("first")])
        scorer = FakeScorer({"failure": ScoringError("A safe API failure")})
        result = score_articles(scorer)
        self.assertEqual((result.attempted, result.scored, len(result.errors)), (3, 2, 1))
        self.assertEqual(len(pending_articles(scorer.config, 20)), 1)
        fixed = FakeScorer()
        result = score_articles(fixed)
        self.assertEqual((result.attempted, result.scored, result.already_scored), (1, 1, 2))
        self.assertEqual(json.loads(fixed.inputs[0])["article"]["provider_article_id"], "failure")

    def test_bad_fake_output_and_invented_tickers_are_not_saved(self):
        save_articles([source("malformed"), source("invented")])
        scorer = FakeScorer({"malformed": {"importance_score": 99},
                            "invented": judgment(primary_tickers=["NVDA"])})
        result = score_articles(scorer)
        self.assertEqual((result.scored, len(result.errors)), (0, 2))
        self.assertEqual(count_scored(scorer.config), 0)

    def test_limit_only_counts_pending_articles(self):
        save_articles([source(str(i)) for i in range(4)])
        scorer = FakeScorer()
        self.assertEqual(score_articles(scorer, limit=2).scored, 2)
        self.assertEqual(score_articles(scorer, limit=2).scored, 2)
        self.assertEqual(score_articles(scorer, limit=2).already_scored, 4)

    def test_overlapping_scoring_is_blocked_without_locking_watchlist_writes(self):
        save_articles([source()])
        scorer = FakeScorer()
        with scoring_lock():
            with self.assertRaisesRegex(ScoringError, "Another scorer"):
                score_articles(scorer)
            database.add_ticker("MSFT")
            self.assertEqual(self.client.get("/news").status_code, 200)
        self.assertEqual(scorer.inputs, [])
        self.assertEqual(score_articles(scorer).scored, 1)

    def test_empty_watchlist_needs_zero_relevance(self):
        database.remove_ticker(database.list_tickers()[0]["id"])
        save_articles([source()])
        self.assertEqual(len(score_articles(FakeScorer()).errors), 1)
        low = judgment(ticker_relevance_score=0, should_surface=False)
        self.assertEqual(score_articles(FakeScorer({"one": low})).scored, 1)

    def test_all_and_surfaced_pages_keep_source_visible_and_escape_analysis(self):
        save_articles([source("important"), source("noise"), source("unscored")])
        score_articles(FakeScorer({
            "important": judgment(why_it_matters="<script>untrusted</script>"),
            "noise": judgment(content_type="evergreen", should_surface=False, importance_score=10, novelty_score=10),
            "unscored": ScoringError("Refused"),
        }))
        with patch("app.analysis.openai_provider.OpenAIArticleScorer.score", side_effect=AssertionError("No web API calls")):
            all_page = self.client.get("/news")
            surfaced = self.client.get("/news?view=surfaced")
        self.assertEqual(all_page.status_code, 200)
        for text in ("Source important", "Source noise", "Source unscored", "AI importance: 80/100",
                     "company event", "negative", "&lt;script&gt;untrusted&lt;/script&gt;",
                     "Company cuts guidance", "https://example.com/source", "Not scored"):
            self.assertIn(text, all_page.text)
        self.assertNotIn("<script>", all_page.text)
        self.assertIn("Source important", surfaced.text)
        self.assertNotIn("Source noise", surfaced.text)
        self.assertNotIn("Source unscored", surfaced.text)
        self.assertEqual(self.client.get("/news?view=unknown").status_code, 422)
        with patch.dict(os.environ, {"OPENAI_MODEL": "new-model"}):
            self.assertIn("Not scored", self.client.get("/news").text)
            self.assertIn("No surfaced articles", self.client.get("/news?view=surfaced").text)
        self.assertEqual(count_scored(current_config()), 2)

    def test_filter_before_pagination_and_preserve_filter_links(self):
        save_articles([source(str(i)) for i in range(53)])
        scorer = FakeScorer({"52": judgment(should_surface=False, importance_score=10)})
        score_articles(scorer, limit=100)
        first = self.client.get("/news?view=surfaced")
        second = self.client.get("/news?view=surfaced&page=2")
        self.assertEqual(first.text.count("<article>"), 50)
        self.assertEqual(second.text.count("<article>"), 2)
        self.assertIn('href="/news?page=2&amp;view=surfaced"', first.text)
        self.assertIn('href="/news?page=1&amp;view=surfaced"', second.text)
        self.assertNotIn("Source 52", first.text)

    def run_command(self, args=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args or [])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_missing_key_and_no_work_do_not_construct_client(self):
        with patch("app.analysis.scorer.OpenAI") as client:
            code, stdout, stderr = self.run_command()
            self.assertEqual(code, 0)
            self.assertIn("No pending articles", stdout)
            save_articles([source()])
            code, stdout, stderr = self.run_command()
            self.assertEqual(code, 1)
            self.assertIn("OPENAI_API_KEY", stderr)
            client.assert_not_called()

    def test_cli_summary_environment_and_partial_failure_exit(self):
        save_articles([source("ok"), source("fail")])
        scorer = FakeScorer({"fail": ScoringError("A safe failure")})
        with patch.dict(os.environ, {"OPENAI_API_KEY": " test-secret ", "OPENAI_MODEL": "custom-model"}), \
                patch("app.analysis.scorer.OpenAI") as client, \
                patch("app.analysis.scorer.OpenAIArticleScorer", return_value=scorer) as factory:
            code, stdout, stderr = self.run_command(["--limit", "2"])
            self.assertEqual(code, 1)
            for line in ("Articles attempted: 2", "scored: 1", "already scored: 0", "failed: 1", "surfaced: 1"):
                self.assertIn(line, stdout)
            client.assert_called_once_with(api_key="test-secret", timeout=30.0, max_retries=0)
            self.assertEqual(factory.call_args.args[1].model, "custom-model")
            self.assertNotIn("test-secret", stdout + stderr)

    def test_cli_invalid_limit(self):
        for value in ("0", "-1", "1001", "abc"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(["--limit", value])
            self.assertEqual(caught.exception.code, 2)

    def test_cli_shows_sanitized_openai_diagnostics(self):
        import httpx
        from openai import OpenAI

        save_articles([source()])
        def handler(request):
            return httpx.Response(400, headers={"x-request-id": "req_cli"}, json={"error": {
                "message": "Unsupported parameter temperature (test-secret)",
                "code": "unsupported_parameter", "param": "temperature",
            }})
        client = OpenAI(api_key="test-secret", max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}), \
                patch("app.analysis.scorer.OpenAI", return_value=client):
            code, stdout, stderr = self.run_command()
        self.assertEqual(code, 1)
        self.assertIn("failed: 1", stdout)
        for expected in ("BadRequestError", "400", "unsupported_parameter", "temperature", "Unsupported parameter", "req_cli"):
            self.assertIn(expected, stderr)
        self.assertNotIn("test-secret", stdout + stderr)
        self.assertEqual(count_scored(current_config()), 0)


if __name__ == "__main__":
    unittest.main()
