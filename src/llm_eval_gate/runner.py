"""Runs a classifier over the evaluation cases and produces a RunRecord.

Order is deterministic (cases sorted by id, repeats in order), so two runs of the same
deterministic candidate are byte-identical apart from wall-clock latency. Progress is
published to listeners (Observer), which keeps printing and logging out of the loop.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Protocol, TextIO

from llm_eval_gate.classifiers import Classifier
from llm_eval_gate.config import CandidateConfig, Prices, SampleSpec
from llm_eval_gate.domain import Case, Observation, RunRecord
from llm_eval_gate.rng import Rng


class RunListener(Protocol):
    def on_start(self, candidate: str, total: int) -> None: ...

    def on_observation(self, observation: Observation, done: int, total: int) -> None: ...

    def on_finish(self, record: RunRecord) -> None: ...


class NullListener:
    def on_start(self, candidate: str, total: int) -> None:
        """Nothing to report."""

    def on_observation(self, observation: Observation, done: int, total: int) -> None:
        """Nothing to report."""

    def on_finish(self, record: RunRecord) -> None:
        """Nothing to report."""


class ProgressPrinter:
    """Prints a progress line every `every` observations. Live runs take minutes."""

    def __init__(self, stream: TextIO | None = None, every: int = 25) -> None:
        self._stream = stream or sys.stderr
        self._every = max(1, every)
        self._name = ""

    def on_start(self, candidate: str, total: int) -> None:
        self._name = candidate
        print(f"[{candidate}] {total} classifications", file=self._stream)

    def on_observation(
        self,
        observation: Observation,  # noqa: ARG002 - listener protocol
        done: int,
        total: int,
    ) -> None:
        if done % self._every == 0 or done == total:
            print(f"[{self._name}] {done}/{total}", file=self._stream)

    def on_finish(self, record: RunRecord) -> None:
        print(
            f"[{record.candidate}] done: {len(record.observations)} observations", file=self._stream
        )


def stratified_sample(cases: Sequence[Case], spec: SampleSpec) -> list[Case]:
    """Same fraction of every (split, label) cell, so a sample never loses a class."""
    ordered = sorted(cases, key=lambda case: case.id)
    if spec.fraction >= 1.0:
        return ordered
    cells: dict[tuple[str, str], list[Case]] = {}
    for case in ordered:
        cells.setdefault((case.split, case.label.value), []).append(case)
    chosen: list[Case] = []
    for key in sorted(cells):
        members = cells[key]
        size = max(1, round(len(members) * spec.fraction))
        chosen.extend(Rng("sample", spec.seed, *key).sample(members, size))
    return sorted(chosen, key=lambda case: case.id)


class EvalRunner:
    def __init__(
        self,
        classifier: Classifier,
        *,
        prices: Prices | None = None,
        listener: RunListener | None = None,
    ) -> None:
        self._classifier = classifier
        self._prices = prices or Prices()
        self._listener: RunListener = listener or NullListener()

    def run(self, config: CandidateConfig, cases: Sequence[Case], dataset_sha256: str) -> RunRecord:
        repeats = 1 if self._classifier.deterministic else config.repeats
        selected = stratified_sample(cases, config.sample)
        total = len(selected) * repeats
        self._listener.on_start(config.name, total)
        observations: list[Observation] = []
        for case in selected:
            for repeat in range(repeats):
                outcome = self._classifier.classify(case, repeat)
                observation = Observation(
                    case_id=case.id,
                    repeat=repeat,
                    predicted=outcome.prediction.value,
                    latency_ms=outcome.latency_ms,
                    tokens_in=outcome.tokens_in,
                    tokens_out=outcome.tokens_out,
                    cost_usd=self._prices.cost(outcome.tokens_in, outcome.tokens_out),
                )
                observations.append(observation)
                self._listener.on_observation(observation, len(observations), total)
        record = RunRecord(
            candidate=config.name,
            stage=config.stage,
            classifier=dict(self._classifier.descriptor),
            dataset_sha256=dataset_sha256,
            repeats=repeats,
            observations=tuple(observations),
        )
        self._listener.on_finish(record)
        return record
