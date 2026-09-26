"""The gate: checks, a three-valued verdict and the report.

The decision rule for regression is non-inferiority with a margin (ADR-0002). With the
paired per-case difference Delta = candidate - reference and its interval [lo, hi] at
one-sided level alpha on each side (Tango score interval by default, see stats.py):

    lo >= -margin   -> pass          the data rules out a regression larger than the margin
    hi <  -margin   -> fail          the data rules out that the regression is within it
    otherwise       -> inconclusive  the data cannot tell, usually because n is too small

`inconclusive` is a first-class outcome, not a rounding of `pass`. The policy decides
whether it blocks. A gate that turns "I cannot tell" into green is how regressions ship.

Checks follow one protocol and are composed by the engine; every check runs, so the report
is complete even when the first one already fails.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from llm_eval_gate.domain import STAGE_PRODUCTION, Case, RunRecord
from llm_eval_gate.metrics import CaseStat, SplitMetrics, metric_value
from llm_eval_gate.spec import Policy, Requirement, Spec
from llm_eval_gate.stats import METHOD_TANGO, McNemar, mcnemar_exact, paired_interval


class Verdict(StrEnum):
    PASS = "pass"  # noqa: S105 - a verdict, not a password
    INCONCLUSIVE = "inconclusive"
    FAIL = "fail"


_SEVERITY = {Verdict.PASS: 0, Verdict.INCONCLUSIVE: 1, Verdict.FAIL: 2}


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    return max(verdicts, key=_SEVERITY.__getitem__, default=Verdict.PASS)


def decide(lo: float, hi: float, margin: float) -> Verdict:
    if lo >= -margin:
        return Verdict.PASS
    if hi < -margin:
        return Verdict.FAIL
    return Verdict.INCONCLUSIVE


@dataclass(frozen=True, slots=True)
class Paired:
    reference_rate: float
    candidate_rate: float
    reference_correct: bool
    candidate_correct: bool


@dataclass(frozen=True, slots=True)
class NonInferiority:
    split: str
    n_cases: int
    delta: float
    lo: float
    hi: float
    margin: float
    alpha: float
    method: str
    resamples: int
    verdict: Verdict
    mcnemar: McNemar

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "n_cases": self.n_cases,
            "delta": self.delta,
            "lo": self.lo,
            "hi": self.hi,
            "margin": self.margin,
            "alpha": self.alpha,
            "method": self.method,
            "resamples": self.resamples,
            "verdict": self.verdict.value,
            "mcnemar": {
                "worse": self.mcnemar.worse,
                "better": self.mcnemar.better,
                "p_worse": self.mcnemar.p_worse,
                "p_two_sided": self.mcnemar.p_two_sided,
            },
        }


def non_inferiority(
    pairs: Sequence[Paired],
    *,
    split: str,
    margin: float,
    alpha: float,
    method: str = METHOD_TANGO,
    resamples: int = 2000,
    seed: int | str = 0,
) -> NonInferiority:
    """`resamples` and `seed` only matter for the bootstrap method."""
    if not pairs:
        raise ValueError("non-inferiority needs at least one paired case")
    diffs = [pair.candidate_rate - pair.reference_rate for pair in pairs]
    interval = paired_interval(diffs, alpha=alpha, method=method, resamples=resamples, seed=seed)
    worse = sum(1 for p in pairs if p.reference_correct and not p.candidate_correct)
    better = sum(1 for p in pairs if not p.reference_correct and p.candidate_correct)
    return NonInferiority(
        split=split,
        n_cases=len(pairs),
        delta=interval.delta,
        lo=interval.lo,
        hi=interval.hi,
        margin=margin,
        alpha=alpha,
        method=interval.method,
        resamples=interval.resamples,
        verdict=decide(interval.lo, interval.hi, margin),
        mcnemar=mcnemar_exact(worse, better),
    )


def pair_stats(
    reference: Mapping[str, CaseStat], candidate: Mapping[str, CaseStat], split: str
) -> list[Paired]:
    shared = sorted(
        case_id
        for case_id, stat in candidate.items()
        if stat.split == split and case_id in reference
    )
    return [
        Paired(
            reference[case_id].correct_rate,
            candidate[case_id].correct_rate,
            reference[case_id].majority_correct,
            candidate[case_id].majority_correct,
        )
        for case_id in shared
    ]


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    kind: str
    title: str
    verdict: Verdict
    enforced: bool
    summary: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "title": self.title,
            "verdict": self.verdict.value,
            "enforced": self.enforced,
            "summary": self.summary,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class GateContext:
    candidate: str
    stage: str
    cases: Sequence[Case]
    run: RunRecord
    reference: RunRecord | None
    metrics: Mapping[str, SplitMetrics]
    run_stats: Mapping[str, CaseStat]
    reference_stats: Mapping[str, CaseStat] | None
    spec: Spec
    policy: Policy


class Check(Protocol):
    def evaluate(self, ctx: GateContext) -> CheckResult: ...


class CompatibilityCheck:
    """Refuses to compare a run with a reference produced on different cases."""

    def evaluate(self, ctx: GateContext) -> CheckResult:
        title = "Referência compatível"
        if ctx.reference is None:
            production = ctx.stage == STAGE_PRODUCTION
            return CheckResult(
                "compatibility",
                "compatibility",
                title,
                Verdict.FAIL if production else Verdict.PASS,
                enforced=production,
                summary=(
                    "candidato em produção sem referência aceita"
                    if production
                    else "sem referência: primeira avaliação, nada a comparar"
                ),
            )
        problems: list[str] = []
        if ctx.reference.dataset_sha256 != ctx.run.dataset_sha256:
            problems.append("dataset mudou desde a referência")
        if ctx.reference.candidate != ctx.run.candidate:
            problems.append("referência pertence a outro candidato")
        if ctx.reference.case_ids != ctx.run.case_ids:
            problems.append("conjunto de casos difere (amostra ou dataset)")
        return CheckResult(
            "compatibility",
            "compatibility",
            title,
            Verdict.FAIL if problems else Verdict.PASS,
            enforced=True,
            summary="; ".join(problems) if problems else "mesmos casos e mesmo dataset",
            details={"problems": problems},
        )


class NonInferiorityCheck:
    def __init__(self, split: str) -> None:
        self.split = split

    def evaluate(self, ctx: GateContext) -> CheckResult:
        check_id = f"non-inferiority:{self.split}"
        title = f"Não inferioridade vs referência [{self.split}]"
        if ctx.reference is None or ctx.reference_stats is None:
            return CheckResult(
                check_id, "non-inferiority", title, Verdict.PASS, False, "sem referência"
            )
        pairs = pair_stats(ctx.reference_stats, ctx.run_stats, self.split)
        if not pairs:
            return CheckResult(
                check_id,
                "non-inferiority",
                title,
                Verdict.INCONCLUSIVE,
                True,
                "nenhum caso pareado",
            )
        regression = ctx.policy.regression
        result = non_inferiority(
            pairs,
            split=self.split,
            margin=regression.margin,
            alpha=regression.alpha,
            method=regression.method,
            resamples=regression.resamples,
            seed=f"{regression.seed}|{ctx.candidate}|{self.split}",
        )
        level = round((1 - 2 * result.alpha) * 100)
        summary = (
            f"delta {result.delta * 100:+.1f} p.p., IC{level} [{result.lo * 100:+.1f}, "
            f"{result.hi * 100:+.1f}] contra margem -{result.margin * 100:.1f} p.p. "
            f"({result.n_cases} casos pareados)"
        )
        return CheckResult(
            check_id, "non-inferiority", title, result.verdict, True, summary, result.to_dict()
        )


class RequirementCheck:
    def __init__(self, requirement: Requirement) -> None:
        self.requirement = requirement

    def evaluate(self, ctx: GateContext) -> CheckResult:
        req = self.requirement
        enforced = req.enforcement == "block" and ctx.stage == STAGE_PRODUCTION
        metrics = ctx.metrics.get(req.split)
        measured = metric_value(metrics, req.metric, req.label) if metrics is not None else None
        details: dict[str, Any] = {"criterion": req.criterion(), "enforcement": req.enforcement}
        if measured is None or measured.n == 0:
            verdict = Verdict.FAIL if ctx.policy.require_evidence else Verdict.INCONCLUSIVE
            details["status"] = "no_evidence"
            return CheckResult(
                req.id, "requirement", req.title, verdict, enforced, "sem evidência", details
            )
        if req.evidence == "lower":
            compared = measured.lo if measured.lo is not None else measured.value
        elif req.evidence == "upper":
            compared = measured.hi if measured.hi is not None else measured.value
        else:
            compared = measured.value
        met = compared >= req.threshold if req.op == ">=" else compared <= req.threshold
        details.update(
            status="met" if met else "not_met",
            value=measured.value,
            lo=measured.lo,
            hi=measured.hi,
            compared=compared,
            threshold=req.threshold,
            n=measured.n,
        )
        summary = f"{req.criterion()}: medido {compared:.4g} (n={measured.n})"
        return CheckResult(
            req.id,
            "requirement",
            req.title,
            Verdict.PASS if met else Verdict.FAIL,
            enforced,
            summary,
            details,
        )


@dataclass(frozen=True, slots=True)
class GateReport:
    candidate: str
    stage: str
    verdict: Verdict
    blocking: bool
    checks: tuple[CheckResult, ...]
    run_fingerprint: str
    reference_fingerprint: str | None
    spec_id: str
    spec_version: str
    spec_sha256: str
    policy_sha256: str

    def enforced_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.enforced and c.verdict is not Verdict.PASS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "stage": self.stage,
            "verdict": self.verdict.value,
            "blocking": self.blocking,
            "checks": [check.to_dict() for check in self.checks],
            "run_fingerprint": self.run_fingerprint,
            "reference_fingerprint": self.reference_fingerprint,
            "spec": {"id": self.spec_id, "version": self.spec_version, "sha256": self.spec_sha256},
            "policy_sha256": self.policy_sha256,
        }


class GateEngine:
    """Composite of checks. Builds the standard set from the spec and the policy."""

    def __init__(self, checks: Sequence[Check]) -> None:
        self._checks = tuple(checks)

    @classmethod
    def standard(cls, spec: Spec, policy: Policy) -> GateEngine:
        checks: list[Check] = [CompatibilityCheck()]
        checks.extend(NonInferiorityCheck(split) for split in policy.regression.splits)
        checks.extend(RequirementCheck(req) for req in spec.requirements)
        return cls(checks)

    def evaluate(self, ctx: GateContext) -> GateReport:
        results = tuple(check.evaluate(ctx) for check in self._checks)
        verdict = worst(result.verdict for result in results if result.enforced)
        blocking = verdict is Verdict.FAIL or (
            verdict is Verdict.INCONCLUSIVE and ctx.policy.regression.on_inconclusive == "fail"
        )
        return GateReport(
            candidate=ctx.candidate,
            stage=ctx.stage,
            verdict=verdict,
            blocking=blocking,
            checks=results,
            run_fingerprint=ctx.run.fingerprint,
            reference_fingerprint=ctx.reference.fingerprint if ctx.reference else None,
            spec_id=ctx.spec.id,
            spec_version=ctx.spec.version,
            spec_sha256=ctx.spec.sha256,
            policy_sha256=ctx.policy.sha256,
        )
