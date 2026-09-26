"""Builds the classifier for a candidate (Factory + Registry).

The provider mode decides what sits behind an LLM candidate:

- `replay` (default, and the only mode CI uses on pull requests): answers come from the
  committed cassette. No network, no key, deterministic.
- `live`: the real provider, wrapped in a budget guard and bounded retries.
- `record`: live, plus every answer is written to the cassette. Used by the live-eval
  workflow to produce new evidence.

Decorator order is deliberate: Recording(Retry(Budget(real))). Only answers that finally
succeeded are recorded, and the budget counts every call that reached the model.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from llm_eval_gate.baseline.signature import SignatureModel
from llm_eval_gate.classifiers import (
    Classifier,
    LLMClassifier,
    RulesClassifier,
    SyntheticClassifier,
)
from llm_eval_gate.config import CandidateConfig, ConfigError, LLMSettings
from llm_eval_gate.jsonio import read_json
from llm_eval_gate.prompt import load_prompt
from llm_eval_gate.providers.base import Provider
from llm_eval_gate.providers.ollama import DEFAULT_BASE_URL, OllamaProvider
from llm_eval_gate.providers.openai_compat import OpenAICompatibleProvider
from llm_eval_gate.providers.replay import Cassette, RecordingProvider, ReplayProvider
from llm_eval_gate.providers.resilience import BudgetGuard, RetryingProvider


class ProviderMode(StrEnum):
    REPLAY = "replay"
    LIVE = "live"
    RECORD = "record"


class PendingEvidenceError(LookupError):
    """Replay was requested but the candidate has no cassette yet."""


ProviderFactory = Callable[[LLMSettings, Mapping[str, str]], Provider]


def _ollama(settings: LLMSettings, environ: Mapping[str, str]) -> Provider:
    base_url = environ.get("OLLAMA_HOST") or settings.base_url or DEFAULT_BASE_URL
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    return OllamaProvider(base_url, timeout_s=settings.timeout_s)


def _openai(settings: LLMSettings, environ: Mapping[str, str]) -> Provider:
    base_url = environ.get("LEG_OPENAI_BASE_URL") or settings.base_url
    if not base_url:
        raise ConfigError("openai-compatible candidates need `base_url` or LEG_OPENAI_BASE_URL")
    return OpenAICompatibleProvider(
        base_url,
        api_key_env=settings.api_key_env,
        timeout_s=settings.timeout_s,
        environ=dict(environ),
    )


PROVIDERS: dict[str, ProviderFactory] = {"ollama": _ollama, "openai-compatible": _openai}


@dataclass(slots=True)
class Built:
    classifier: Classifier
    cassette: Cassette | None = None
    cassette_path: Path | None = None
    budget: BudgetGuard | None = None

    def persist(self) -> None:
        """Write the cassette after a recording run. No-op in the other modes."""
        if self.cassette is not None and self.cassette_path is not None:
            self.cassette.save(self.cassette_path)


def build_classifier(
    config: CandidateConfig,
    root: Path,
    mode: ProviderMode = ProviderMode.REPLAY,
    *,
    environ: Mapping[str, str] | None = None,
    providers: Mapping[str, ProviderFactory] = PROVIDERS,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> Built:
    env = environ if environ is not None else dict(os.environ)
    if config.kind == "rules":
        assert config.model_path is not None
        model = SignatureModel.from_dict(read_json(root / config.model_path))
        return Built(RulesClassifier(model, clock=clock))
    if config.kind == "synthetic":
        assert config.synthetic is not None
        return Built(SyntheticClassifier(config.synthetic))

    settings = config.llm
    assert settings is not None
    if settings.provider not in providers:
        raise ConfigError(f"unknown provider {settings.provider!r}; known: {sorted(providers)}")
    prompt = load_prompt(root / settings.prompt)
    cassette_path = root / settings.cassette

    def classifier(provider: Provider) -> LLMClassifier:
        return LLMClassifier(
            provider,
            prompt,
            provider_label=settings.provider,
            model=settings.model,
            temperature=settings.temperature,
            seed=settings.seed,
            max_tokens=settings.max_tokens,
        )

    if mode is ProviderMode.REPLAY:
        if not cassette_path.exists():
            raise PendingEvidenceError(f"{config.name}: no cassette at {settings.cassette}")
        return Built(
            classifier(ReplayProvider(Cassette.load(cassette_path), source=settings.cassette))
        )

    budget = BudgetGuard(
        providers[settings.provider](settings, env),
        max_calls=config.budget.max_calls,
        max_tokens=config.budget.max_tokens,
    )
    provider: Provider = RetryingProvider(budget)
    if mode is ProviderMode.LIVE:
        return Built(classifier(provider), budget=budget)
    cassette = Cassette.load(cassette_path) if cassette_path.exists() else Cassette()
    return Built(
        classifier(RecordingProvider(provider, cassette)),
        cassette=cassette,
        cassette_path=cassette_path,
        budget=budget,
    )
