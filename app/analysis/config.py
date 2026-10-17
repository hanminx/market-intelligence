"""One shared model/prompt identity for the command and news page."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.analysis.schema import ArticleAnalysis

DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"
PROMPT = Path(__file__).with_name("prompt_v1.txt").read_text(encoding="utf-8")
REQUEST_SETTINGS = {"temperature": 0, "max_output_tokens": 2000, "store": False}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ScoringConfig:
    model: str = DEFAULT_MODEL
    prompt: str = PROMPT

    @property
    def schema_json(self) -> str:
        return canonical_json(ArticleAnalysis.model_json_schema())

    @property
    def settings_json(self) -> str:
        settings = dict(REQUEST_SETTINGS)
        if self.model == "gpt-5.6" or self.model.startswith("gpt-5.6-"):
            settings.pop("temperature", None)
            settings["reasoning"] = {"effort": "low"}
        return canonical_json(settings)

    @property
    def prompt_version(self) -> str:
        specification = self.prompt + "\n" + self.schema_json + "\n" + self.settings_json
        digest = hashlib.sha256(specification.encode()).hexdigest()[:16]
        return f"article-triage-v1-{digest}"


def current_config() -> ScoringConfig:
    return ScoringConfig(model=os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL)
