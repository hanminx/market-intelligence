"""OpenAI Responses API adapter; no persistence or command-line behavior."""

import json
import re
from urllib.parse import quote

from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError
from pydantic import ValidationError

from app.analysis.config import ScoringConfig
from app.analysis.provider import ScoredArticle, ScoringError
from app.analysis.schema import ArticleAnalysis


def error_diagnostics(error: Exception, api_key: object) -> str:
    """Whitelist SDK error fields; never serialize requests, headers or full errors."""
    secrets = [api_key] if isinstance(api_key, str) and api_key else []
    request = getattr(error, "request", None)
    if request is not None:
        authorization = request.headers.get("authorization", "")
        if authorization:
            secrets.extend([authorization, authorization.split(" ", 1)[-1]])

    def clean(value: object):
        if value is None or not isinstance(value, (str, int)):
            return None
        text = str(value)
        for secret in secrets:
            if secret:
                for variant in (secret, quote(secret, safe=""), json.dumps(secret)[1:-1]):
                    text = text.replace(variant, "[REDACTED]")
        text = re.sub(r"\bsk-[\w*.-]+", "[REDACTED]", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:proxy-)?authorization\b[^\r\n]*", "[REDACTED HEADER]", text, flags=re.IGNORECASE)
        text = re.sub(r"\bbearer\s+[^\s,;\"']+", "[REDACTED]", text, flags=re.IGNORECASE)
        # Remove terminal control characters, normalize lines, and bound API text.
        text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
        return text[:1000]

    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    body = body if isinstance(body, dict) else {}
    fields = {
        "exception_class": type(error).__name__,
        "http_status": getattr(error, "status_code", None),
        "api_error_code": body.get("code", getattr(error, "code", None)),
        "api_error_param": body.get("param", getattr(error, "param", None)),
        "api_error_message": body.get("message"),
        "request_id": getattr(error, "request_id", None),
    }
    return json.dumps({name: clean(value) for name, value in fields.items()}, ensure_ascii=True)


class OpenAIArticleScorer:
    provider = "openai"

    def __init__(self, client: OpenAI, config: ScoringConfig):
        self.client = client
        self.config = config

    def score(self, input_json: str) -> ScoredArticle:
        try:
            response = self.client.responses.parse(
                model=self.config.model,
                input=[
                    {"role": "system", "content": self.config.prompt},
                    {"role": "user", "content": input_json},
                ],
                text_format=ArticleAnalysis,
                **json.loads(self.config.settings_json),
            )
            if response.status != "completed":
                raise ScoringError("OpenAI returned incomplete output; no analysis was saved.")
            for output in response.output:
                if output.type == "message":
                    if any(part.type == "refusal" for part in output.content):
                        raise ScoringError("OpenAI refused this article; no analysis was saved.")
            parsed = response.output_parsed
            if not isinstance(parsed, ArticleAnalysis):
                raise ScoringError("OpenAI returned missing or malformed structured output.")
            # Revalidate even if a client/provider constructed the object without validation.
            analysis = ArticleAnalysis.model_validate(parsed.model_dump())
            return ScoredArticle(analysis, response.model, response.id)
        except APIConnectionError as error:
            raise ScoringError("Could not reach OpenAI (network error or timeout). Rerun later. "
                               + error_diagnostics(error, self.client.api_key)) from None
        except APIStatusError as error:
            if error.status_code in (401, 403):
                message = "OpenAI rejected access. Check OPENAI_API_KEY and model permissions."
            elif error.status_code == 429:
                message = "OpenAI rate or quota limit reached. Check usage and rerun later."
            else:
                message = f"OpenAI request failed (HTTP {error.status_code}). Check model compatibility or retry later."
            raise ScoringError(message + " " + error_diagnostics(error, self.client.api_key)) from None
        except (ValidationError, ValueError, TypeError, AttributeError) as error:
            raise ScoringError("OpenAI returned malformed output or an analysis that failed validation. "
                               + error_diagnostics(error, self.client.api_key)) from None
        except OpenAIError as error:
            raise ScoringError("OpenAI could not complete structured analysis. No analysis was saved. "
                               + error_diagnostics(error, self.client.api_key)) from None
