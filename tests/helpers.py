"""Builders shared by the tests: synthetic runs and a fake LLM provider."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from llm_eval_gate.classifiers import SyntheticClassifier, SyntheticProfile
from llm_eval_gate.config import Budget, CandidateConfig, LLMSettings, Prices, SampleSpec
from llm_eval_gate.domain import STAGE_EXPERIMENTAL, Case, RunRecord
from llm_eval_gate.labels import Label
from llm_eval_gate.providers.base import Completion, CompletionRequest, Provider
from llm_eval_gate.runner import EvalRunner


def synthetic_config(
    name: str = "synthetic-test",
    *,
    accuracy: Mapping[str, float] | None = None,
    stage: str = STAGE_EXPERIMENTAL,
    repeats: int = 1,
    fraction: float = 1.0,
    seed: int = 0,
    correlation: float = 1.0,
    instability: float = 0.0,
    invalid_rate: float = 0.0,
) -> CandidateConfig:
    profile = SyntheticProfile(
        accuracy=dict(accuracy or {"in-dist": 0.97, "hard": 0.8}),
        instability=instability,
        invalid_rate=invalid_rate,
        correlation=correlation,
        seed=seed,
    )
    return CandidateConfig(
        name=name,
        stage=stage,
        description="test",
        kind="synthetic",
        repeats=repeats,
        sample=SampleSpec(fraction=fraction, seed=3),
        prices=Prices(0.1, 0.4),
        budget=Budget(),
        synthetic=profile,
    )


def synthetic_run(cases: Sequence[Case], config: CandidateConfig, dataset_sha256: str) -> RunRecord:
    assert config.synthetic is not None
    return EvalRunner(SyntheticClassifier(config.synthetic), prices=config.prices).run(
        config, cases, dataset_sha256
    )


class KeywordProvider:
    """A deterministic fake LLM: answers from keywords, with a knob for bad output."""

    def __init__(self, broken_every: int = 0) -> None:
        self.calls = 0
        self._broken_every = broken_every

    @property
    def name(self) -> str:
        return "keyword"

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls += 1
        if self._broken_every and self.calls % self._broken_every == 0:
            return Completion("I think it is a pool thing", 300, 8, 900.0)
        text = request.user.lower()
        label = Label.NONE
        for needle, candidate in (
            ("pool", Label.POOL_LOCK),
            ("heap", Label.HEAP_OOM),
            ("retry", Label.RETRY_STORM),
            ("rollback", Label.ROLLBACK),
            ("certif", Label.CERT),
            ("security group", Label.ACL),
            ("sticki", Label.LB_APP),
            ("statist", Label.SLOW_QUERY),
        ):
            if needle in text:
                label = candidate
                break
        body = json.dumps({"label": label.value, "evidence": "keyword"})
        return Completion(body, 300 + len(text) // 4, 12, 850.0 + (request.seed % 3) * 10)


def keyword_factory(settings: LLMSettings, environ: Mapping[str, str]) -> Provider:
    return KeywordProvider()
