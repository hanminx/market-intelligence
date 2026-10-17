import json
import hashlib
import unittest

import httpx
from openai import OpenAI

from app.analysis.config import REQUEST_SETTINGS, ScoringConfig, canonical_json
from app.analysis.openai_provider import OpenAIArticleScorer
from app.analysis.provider import ScoringError
from app.analysis.schema import ArticleAnalysis


def analysis_data():
    return dict(
        primary_tickers=["AAPL"], ticker_relevance_score=10, content_type="evergreen",
        novelty_score=10, fundamental_impact_score=5, market_impact_score=5,
        importance_score=10, impact_direction="neutral", time_horizon="long",
        is_opinion=False, should_surface=False,
        why_it_matters="Historical returns provide no new company catalyst.", confidence_score=90,
    )


def response_payload(content=None, status="completed"):
    if content is None:
        content = [{"type": "output_text", "text": json.dumps(analysis_data()), "annotations": []}]
    return {
        "id": "resp_fake", "object": "response", "created_at": 1770000000,
        "model": "gpt-4.1-mini-2025-04-14", "status": status,
        "output": [{"type": "message", "id": "msg_fake", "role": "assistant",
                    "status": "completed", "content": content}],
        "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
    }


class OpenAIAdapterTests(unittest.TestCase):
    def score_with(self, handler, model=None):
        with OpenAI(api_key="test-secret", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
            config = ScoringConfig(model=model) if model else ScoringConfig()
            return OpenAIArticleScorer(client, config).score('{"article":{"title":"untrusted text"}}')

    def test_gpt56_request_settings_and_existing_fingerprint(self):
        for model in ("gpt-5.6", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna-2026-09-01"):
            def handler(request):
                body = json.loads(request.content)
                self.assertEqual(body["model"], model)
                self.assertEqual(body["reasoning"], {"effort": "low"})
                self.assertEqual(body["max_output_tokens"], 2000)
                self.assertFalse(body["store"])
                self.assertNotIn("temperature", body)
                self.assertEqual(body["input"][0]["content"], ScoringConfig().prompt)
                self.assertEqual(body["text"]["format"]["schema"], ArticleAnalysis.model_json_schema())
                return httpx.Response(200, json=response_payload())
            with self.subTest(model=model):
                self.score_with(handler, model=model)
                self.assertEqual(json.loads(ScoringConfig(model=model).settings_json), {
                    "reasoning": {"effort": "low"}, "max_output_tokens": 2000, "store": False,
                })
        old = ScoringConfig()
        original = canonical_json({"temperature": 0, "max_output_tokens": 2000, "store": False})
        digest = hashlib.sha256((old.prompt + "\n" + old.schema_json + "\n" + original).encode()).hexdigest()[:16]
        self.assertEqual(old.settings_json, original)
        self.assertEqual(old.prompt_version, f"article-triage-v1-{digest}")
        self.assertNotEqual(old.prompt_version, ScoringConfig(model="gpt-5.6-luna").prompt_version)
        self.assertEqual(REQUEST_SETTINGS, json.loads(original))

    def test_api_diagnostic_fields_and_secret_redaction(self):
        def handler(request):
            return httpx.Response(400, headers={"x-request-id": "req_test"}, json={"error": {
                "message": "Unsupported parameter temperature. key=test-secret; other=sk-proj-abcdef***xyz",
                "code": "unsupported_parameter", "param": "temperature",
                "unwanted": "must not be dumped",
            }})
        with self.assertRaises(ScoringError) as caught:
            self.score_with(handler)
        message = str(caught.exception)
        for expected in ("BadRequestError", "400", "unsupported_parameter", "temperature", "Unsupported parameter", "req_test"):
            self.assertIn(expected, message)
        for secret in ("test-secret", "sk-proj-abcdef", "must not be dumped"):
            self.assertNotIn(secret, message)

    def test_headers_control_characters_and_non_json_errors_are_not_dumped(self):
        def handler(request):
            return httpx.Response(401, headers={"x-request-id": "req_\x1b_test"}, json={"error": {
                "message": 'Authorization: Bearer unrelated-secret\nProblem \x1b[31m ' + "x" * 2000,
                "param": "test-secret", "code": "sk-another-secret",
            }})
        with self.assertRaises(ScoringError) as caught:
            self.score_with(handler)
        message = str(caught.exception)
        for forbidden in ("unrelated-secret", "Bearer", "Authorization", "test-secret", "sk-another-secret", "\x1b", "\n"):
            self.assertNotIn(forbidden, message)
        self.assertLess(len(message), 1500)
        with self.assertRaises(ScoringError) as caught:
            self.score_with(lambda request: httpx.Response(500, text="unsafe non-json body test-secret"))
        self.assertNotIn("unsafe non-json body", str(caught.exception))
        self.assertIn('"api_error_message": null', str(caught.exception))

    def test_actual_sdk_structured_request_and_typed_parsing(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            self.assertEqual(request.headers["authorization"], "Bearer test-secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], ScoringConfig().model)
            self.assertEqual(body["input"][0]["role"], "system")
            self.assertEqual(body["input"][0]["content"], ScoringConfig().prompt)
            self.assertEqual(body["input"][1]["role"], "user")
            self.assertIn("untrusted text", body["input"][1]["content"])
            self.assertFalse(body["store"])
            self.assertEqual(body["temperature"], 0)
            self.assertNotIn("tools", body)
            output_format = body["text"]["format"]
            self.assertEqual(output_format["type"], "json_schema")
            self.assertTrue(output_format["strict"])
            self.assertFalse(output_format["schema"]["additionalProperties"])
            self.assertEqual(set(output_format["schema"]["required"]), set(ArticleAnalysis.model_fields))
            return httpx.Response(200, json=response_payload())
        result = self.score_with(handler)
        self.assertEqual(result.analysis.model_dump(), analysis_data())
        self.assertEqual(result.response_id, "resp_fake")

    def test_refused_incomplete_missing_and_invalid_output(self):
        payloads = [
            response_payload([{"type": "refusal", "refusal": "test-secret unsafe raw refusal"}]),
            response_payload(status="incomplete"),
            response_payload([]),
            response_payload([{"type": "output_text", "text": "not json test-secret", "annotations": []}]),
            response_payload([{"type": "output_text", "text": json.dumps({**analysis_data(), "importance_score": 101}), "annotations": []}]),
            response_payload([{"type": "output_text", "text": json.dumps({**analysis_data(), "should_surface": True}), "annotations": []}]),
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ScoringError) as caught:
                self.score_with(lambda request: httpx.Response(200, json=payload))
            self.assertNotIn("test-secret", str(caught.exception))

    def test_api_errors_are_redacted_and_not_retried(self):
        for status, message in ((401, "rejected access"), (403, "rejected access"),
                                (429, "rate or quota"), (500, "HTTP 500"), (400, "HTTP 400")):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={"error": {"message": "test-secret sensitive response"}})
            with self.subTest(status=status), self.assertRaisesRegex(ScoringError, message) as caught:
                self.score_with(handler)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("test-secret", str(caught.exception))

    def test_network_failure(self):
        def handler(request):
            raise httpx.ReadTimeout("test-secret", request=request)
        with self.assertRaisesRegex(ScoringError, "network error or timeout") as caught:
            self.score_with(handler)
        self.assertNotIn("test-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
