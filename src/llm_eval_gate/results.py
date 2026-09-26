"""Result types shared by the pipeline, the reports and the dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_eval_gate.config import CandidateConfig
from llm_eval_gate.domain import RunRecord
from llm_eval_gate.gate import GateReport
from llm_eval_gate.metrics import CaseStat, SplitMetrics
from llm_eval_gate.reference import Reference
from llm_eval_gate.stats import McNemar
from llm_eval_gate.trace import TraceRow

STATUS_EVALUATED = "evaluated"
STATUS_PENDING = "pending"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class CandidateResult:
    config: CandidateConfig
    status: str
    run: RunRecord | None = None
    reference: Reference | None = None
    metrics: Mapping[str, SplitMetrics] = field(default_factory=dict)
    report: GateReport | None = None
    trace: tuple[TraceRow, ...] = ()
    run_stats: Mapping[str, CaseStat] = field(default_factory=dict)
    reference_stats: Mapping[str, CaseStat] = field(default_factory=dict)
    message: str = ""

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def blocking(self) -> bool:
        if self.report is not None:
            return self.report.blocking
        if self.status == STATUS_ERROR:
            return True
        # Evidence that existed and disappeared is a failure, not a pending candidate:
        # otherwise deleting the cassette would be the cheapest way past the gate.
        return self.status == STATUS_PENDING and self.reference is not None

    @property
    def outcome(self) -> str:
        if self.report is not None:
            return self.report.verdict.value
        if self.status == STATUS_PENDING and self.reference is not None:
            return "fail"
        return self.status

    def summary_dict(self) -> dict[str, Any]:
        splits = {
            split: {
                "accuracy": m.accuracy,
                "accuracy_lo": m.accuracy_lo,
                "accuracy_hi": m.accuracy_hi,
                "macro_f1": m.macro_f1,
                "format_valid_rate": m.format_valid_rate,
                "instability_rate": m.instability_rate,
                "latency_p95_ms": m.latency_p95_ms,
                "cost_per_call_usd": m.cost_per_call_usd,
                "n_cases": m.n_cases,
            }
            for split, m in self.metrics.items()
        }
        return {
            "name": self.name,
            "stage": self.config.stage,
            "kind": self.config.kind,
            "description": self.config.description,
            "status": self.status,
            "outcome": self.outcome,
            "blocking": self.blocking,
            "message": self.message,
            "has_reference": self.reference is not None,
            "splits": splits,
            "report": self.report.to_dict() if self.report else None,
            "trace": [row.to_dict() for row in self.trace],
        }


@dataclass(frozen=True, slots=True)
class CIOutcome:
    results: tuple[CandidateResult, ...]
    problems: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        if self.problems:
            return 1
        return 1 if any(result.blocking for result in self.results) else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "problems": list(self.problems),
            "candidates": [result.summary_dict() for result in self.results],
        }


@dataclass(frozen=True, slots=True)
class ClassComparison:
    label: str
    support: int
    a_recall: float
    b_recall: float
    p_two_sided: float
    p_holm: float


@dataclass(frozen=True, slots=True)
class Comparison:
    """B measured against A on the cases both evaluated, paired case by case."""

    a: str
    b: str
    split: str
    n_cases: int
    a_accuracy: float
    b_accuracy: float
    delta: float
    lo: float
    hi: float
    mcnemar: McNemar
    classes: tuple[ClassComparison, ...]
    operators: tuple[tuple[str, int, float, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "split": self.split,
            "n_cases": self.n_cases,
            "a_accuracy": self.a_accuracy,
            "b_accuracy": self.b_accuracy,
            "delta": self.delta,
            "lo": self.lo,
            "hi": self.hi,
            "mcnemar": {
                "a_right_b_wrong": self.mcnemar.worse,
                "a_wrong_b_right": self.mcnemar.better,
                "p_two_sided": self.mcnemar.p_two_sided,
            },
            "classes": [
                {
                    "label": c.label,
                    "support": c.support,
                    "a_recall": c.a_recall,
                    "b_recall": c.b_recall,
                    "p_two_sided": c.p_two_sided,
                    "p_holm": c.p_holm,
                }
                for c in self.classes
            ],
            "operators": [
                {"operator": op, "cases": n, "a_accuracy": a_acc, "b_accuracy": b_acc}
                for op, n, a_acc, b_acc in self.operators
            ],
        }
