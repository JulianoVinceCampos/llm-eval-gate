"""Didactic scenarios: synthetic reference and candidate pairs with a known outcome.

Each scenario states the verdict it must produce (`expected`), and the test suite holds the
gate to it. They double as the dashboard's teaching material: the same regression reads
`fail` with 300 cases and `inconclusive` with 30, which is the whole argument for sample
size in one screen.

They are not candidates. Nothing here is gated in CI, and nothing here is a language model.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_eval_gate.classifiers import SyntheticClassifier, SyntheticProfile
from llm_eval_gate.config import Budget, CandidateConfig, ConfigError, Prices, SampleSpec
from llm_eval_gate.domain import EVAL_SPLITS, STAGE_EXPERIMENTAL, Case, RunRecord
from llm_eval_gate.gate import NonInferiority, Verdict, non_inferiority, pair_stats, worst
from llm_eval_gate.jsonio import read_text
from llm_eval_gate.metrics import case_stats
from llm_eval_gate.runner import EvalRunner
from llm_eval_gate.spec import RegressionPolicy

_PROFILE_KEYS = {"accuracy", "instability", "invalid_rate", "correlation", "latency_ms", "seed"}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    title: str
    story: str
    expected: Verdict
    fraction: float
    repeats: int
    reference: SyntheticProfile
    candidate: SyntheticProfile


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    scenario: Scenario
    verdict: Verdict
    splits: tuple[NonInferiority, ...]

    @property
    def as_expected(self) -> bool:
        return self.verdict is self.scenario.expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.scenario.name,
            "title": self.scenario.title,
            "story": self.scenario.story,
            "expected": self.scenario.expected.value,
            "verdict": self.verdict.value,
            "as_expected": self.as_expected,
            "fraction": self.scenario.fraction,
            "splits": [item.to_dict() for item in self.splits],
        }


def _profile(table: Mapping[str, Any], where: str) -> SyntheticProfile:
    unknown = sorted(set(table) - _PROFILE_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    accuracy = table.get("accuracy")
    if not isinstance(accuracy, dict) or not accuracy:
        raise ConfigError(f"{where}: `accuracy` table per split is required")
    return SyntheticProfile(
        accuracy={str(k): float(v) for k, v in accuracy.items()},
        instability=float(table.get("instability", 0.0)),
        invalid_rate=float(table.get("invalid_rate", 0.0)),
        correlation=float(table.get("correlation", 1.0)),
        latency_ms=float(table.get("latency_ms", 400.0)),
        seed=int(table.get("seed", 0)),
    )


def load_scenario(path: Path) -> Scenario:
    data = tomllib.loads(read_text(path))
    head = data.get("scenario")
    if not isinstance(head, dict):
        raise ConfigError(f"{path.name}: missing [scenario]")
    reference = data.get("reference")
    candidate = data.get("candidate")
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        raise ConfigError(f"{path.name}: [reference] and [candidate] are required")
    return Scenario(
        name=str(head["name"]),
        title=str(head["title"]),
        story=str(head.get("story", "")),
        expected=Verdict(str(head["expected"])),
        fraction=float(head.get("fraction", 1.0)),
        repeats=int(head.get("repeats", 1)),
        reference=_profile(reference, f"{path.name} [reference]"),
        candidate=_profile(candidate, f"{path.name} [candidate]"),
    )


def load_scenarios(directory: Path) -> list[Scenario]:
    return [load_scenario(path) for path in sorted(directory.glob("*.toml"))]


def _run(
    profile: SyntheticProfile, scenario: Scenario, cases: Sequence[Case], role: str
) -> RunRecord:
    config = CandidateConfig(
        name=f"{scenario.name}-{role}",
        stage=STAGE_EXPERIMENTAL,
        description="scenario",
        kind="synthetic",
        repeats=scenario.repeats,
        sample=SampleSpec(fraction=scenario.fraction, seed=17),
        prices=Prices(),
        budget=Budget(),
        synthetic=profile,
    )
    return EvalRunner(SyntheticClassifier(profile)).run(config, cases, "scenario")


def evaluate_scenario(
    scenario: Scenario, cases: Sequence[Case], policy: RegressionPolicy
) -> ScenarioOutcome:
    reference = case_stats(cases, _run(scenario.reference, scenario, cases, "reference"))
    candidate = case_stats(cases, _run(scenario.candidate, scenario, cases, "candidate"))
    results: list[NonInferiority] = []
    for split in EVAL_SPLITS:
        pairs = pair_stats(reference, candidate, split)
        if pairs:
            results.append(
                non_inferiority(
                    pairs,
                    split=split,
                    margin=policy.margin,
                    alpha=policy.alpha,
                    method=policy.method,
                    resamples=policy.resamples,
                    seed=f"scenario|{scenario.name}|{split}",
                )
            )
    return ScenarioOutcome(scenario, worst(r.verdict for r in results), tuple(results))
