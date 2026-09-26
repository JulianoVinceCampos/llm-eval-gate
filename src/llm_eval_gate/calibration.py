"""Meta-evaluation: test the gate against models whose true effect is known.

A gate is itself a measurement instrument, and an instrument nobody calibrated is an
opinion. Here the reference and the candidate are simulated with a known accuracy gap
(`effect`), over and over, and each trial is decided with exactly the code the CI runs.
The resulting table answers the questions that matter before trusting a red build:

- with no real regression, how often does the gate block anyway? (friction, false alarm)
- with a regression exactly at the margin, how often does it let it through? This is the
  error the margin promises to bound, so it should sit near alpha. (false pass)
- with a clear regression, how often does it catch it, and from which sample size? (power)

Every trial is decided by every method in `methods` on the same simulated pairs, so the
comparison between methods is paired too. That comparison is how the project chose its
interval (ADR-0002): the percentile bootstrap collapses when few cases disagree and passes
regressions it cannot see, Wald+2 is close but estimates the variance where the data
landed instead of under the null, and the Tango score test is the one that holds.

Paired structure matters: a real model update is right and wrong on mostly the same cases.
`correlation` is the share of cases where both models share the latent difficulty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from llm_eval_gate.gate import Paired, Verdict, non_inferiority
from llm_eval_gate.jsonio import SchemaError
from llm_eval_gate.rng import Rng
from llm_eval_gate.stats import PAIRED_METHODS

CALIBRATION_SCHEMA = 2


@dataclass(frozen=True, slots=True)
class CalibrationSpec:
    trials: int = 400
    sizes: tuple[int, ...] = (20, 50, 100, 300)
    effects: tuple[float, ...] = (0.0, -0.015, -0.03, -0.06, -0.12)
    methods: tuple[str, ...] = PAIRED_METHODS
    reference_accuracy: float = 0.85
    correlation: float = 0.8
    margin: float = 0.03
    alpha: float = 0.05
    resamples: int = 500
    seed: int = 2718

    def __post_init__(self) -> None:
        if self.trials < 1 or not self.sizes or not self.effects or not self.methods:
            raise ValueError("calibration needs trials, sizes, effects and methods")
        unknown = [m for m in self.methods if m not in PAIRED_METHODS]
        if unknown:
            raise ValueError(f"unknown methods {unknown}; known: {list(PAIRED_METHODS)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trials": self.trials,
            "sizes": list(self.sizes),
            "effects": list(self.effects),
            "methods": list(self.methods),
            "reference_accuracy": self.reference_accuracy,
            "correlation": self.correlation,
            "margin": self.margin,
            "alpha": self.alpha,
            "resamples": self.resamples,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationSpec:
        return cls(
            trials=int(data["trials"]),
            sizes=tuple(int(n) for n in data["sizes"]),
            effects=tuple(float(e) for e in data["effects"]),
            methods=tuple(str(m) for m in data["methods"]),
            reference_accuracy=float(data["reference_accuracy"]),
            correlation=float(data["correlation"]),
            margin=float(data["margin"]),
            alpha=float(data["alpha"]),
            resamples=int(data["resamples"]),
            seed=int(data["seed"]),
        )


@dataclass(frozen=True, slots=True)
class CalibrationCell:
    method: str
    n: int
    effect: float
    trials: int
    passes: int
    inconclusive: int
    fails: int

    @property
    def pass_rate(self) -> float:
        return self.passes / self.trials

    @property
    def inconclusive_rate(self) -> float:
        return self.inconclusive / self.trials

    @property
    def fail_rate(self) -> float:
        return self.fails / self.trials

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "n": self.n,
            "effect": self.effect,
            "trials": self.trials,
            "pass": self.passes,
            "inconclusive": self.inconclusive,
            "fail": self.fails,
        }


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-12


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    spec: CalibrationSpec
    cells: tuple[CalibrationCell, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CALIBRATION_SCHEMA,
            "spec": self.spec.to_dict(),
            "cells": [cell.to_dict() for cell in self.cells],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationResult:
        if int(data.get("schema", 0)) != CALIBRATION_SCHEMA:
            raise SchemaError(
                f"calibration schema {data.get('schema')!r}, expected {CALIBRATION_SCHEMA}; "
                "run `llm-eval-gate calibrate`"
            )
        cells = tuple(
            CalibrationCell(
                method=str(c["method"]),
                n=int(c["n"]),
                effect=float(c["effect"]),
                trials=int(c["trials"]),
                passes=int(c["pass"]),
                inconclusive=int(c["inconclusive"]),
                fails=int(c["fail"]),
            )
            for c in data["cells"]
        )
        return cls(CalibrationSpec.from_dict(data["spec"]), cells)

    def cell(self, method: str, n: int, effect: float) -> CalibrationCell | None:
        return next(
            (c for c in self.cells if c.method == method and c.n == n and _same(c.effect, effect)),
            None,
        )

    def cells_at(self, method: str, effect: float) -> list[CalibrationCell]:
        return [c for c in self.cells if c.method == method and _same(c.effect, effect)]

    def worst_false_pass(self, method: str) -> float:
        """Largest pass rate when the true regression equals the margin. Should be ~alpha."""
        return max((c.pass_rate for c in self.cells_at(method, -self.spec.margin)), default=0.0)

    def worst_false_alarm(self, method: str) -> float:
        """Largest `fail` rate when there is no regression at all."""
        return max((c.fail_rate for c in self.cells_at(method, 0.0)), default=0.0)


def simulate_pairs(spec: CalibrationSpec, n: int, effect: float, trial: int) -> list[Paired]:
    draw = Rng("calibration", spec.seed, n, effect, trial).stream()
    p_reference = spec.reference_accuracy
    p_candidate = min(1.0, max(0.0, p_reference + effect))
    pairs: list[Paired] = []
    for _ in range(n):
        latent = draw()
        candidate_latent = latent if draw() < spec.correlation else draw()
        reference_ok = latent < p_reference
        candidate_ok = candidate_latent < p_candidate
        pairs.append(Paired(float(reference_ok), float(candidate_ok), reference_ok, candidate_ok))
    return pairs


def decide_trial(spec: CalibrationSpec, pairs: list[Paired], method: str, seed: str) -> Verdict:
    return non_inferiority(
        pairs,
        split="calibration",
        margin=spec.margin,
        alpha=spec.alpha,
        method=method,
        resamples=spec.resamples,
        seed=seed,
    ).verdict


def run_calibration(
    spec: CalibrationSpec, progress: Callable[[int, int], None] | None = None
) -> CalibrationResult:
    cells: list[CalibrationCell] = []
    total = len(spec.sizes) * len(spec.effects)
    done = 0
    for n in spec.sizes:
        for effect in spec.effects:
            counts = {
                method: {Verdict.PASS: 0, Verdict.INCONCLUSIVE: 0, Verdict.FAIL: 0}
                for method in spec.methods
            }
            for trial in range(spec.trials):
                pairs = simulate_pairs(spec, n, effect, trial)
                seed = f"calibration|{spec.seed}|{n}|{effect}|{trial}"
                for method in spec.methods:
                    counts[method][decide_trial(spec, pairs, method, seed)] += 1
            for method in spec.methods:
                tally = counts[method]
                cells.append(
                    CalibrationCell(
                        method=method,
                        n=n,
                        effect=effect,
                        trials=spec.trials,
                        passes=tally[Verdict.PASS],
                        inconclusive=tally[Verdict.INCONCLUSIVE],
                        fails=tally[Verdict.FAIL],
                    )
                )
            done += 1
            if progress is not None:
                progress(done, total)
    return CalibrationResult(spec, tuple(cells))
