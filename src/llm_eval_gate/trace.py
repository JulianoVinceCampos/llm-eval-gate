"""Traceability: requirement -> evidence cases -> measured value -> status.

The matrix lists every requirement of the spec, including the ones with no evidence, and
for each one the cases that pull the number down. That second column is what turns a red
gate into something an engineer can act on in five minutes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from llm_eval_gate.gate import CheckResult
from llm_eval_gate.labels import INVALID
from llm_eval_gate.metrics import CaseStat
from llm_eval_gate.spec import Requirement, Spec

MAX_CASES = 10


@dataclass(frozen=True, slots=True)
class TraceRow:
    requirement: Requirement
    status: str
    enforced: bool
    measured: float | None
    compared: float | None
    n: int
    failing_cases: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.requirement.id,
            "title": self.requirement.title,
            "criterion": self.requirement.criterion(),
            "enforcement": self.requirement.enforcement,
            "status": self.status,
            "enforced": self.enforced,
            "measured": self.measured,
            "compared": self.compared,
            "n": self.n,
            "failing_cases": list(self.failing_cases),
        }


def _failing(requirement: Requirement, stats: Sequence[CaseStat]) -> tuple[str, ...]:
    in_split = [s for s in stats if s.split == requirement.split]
    metric = requirement.metric
    if metric in {"accuracy", "majority_accuracy", "macro_f1"}:
        picked = [s for s in in_split if not s.majority_correct]
    elif metric == "recall":
        picked = [
            s for s in in_split if s.label.value == requirement.label and not s.majority_correct
        ]
    elif metric == "precision":
        picked = [s for s in in_split if s.majority == requirement.label and not s.majority_correct]
    elif metric == "format_valid_rate":
        picked = [s for s in in_split if INVALID in s.predictions]
    elif metric == "instability_rate":
        picked = [s for s in in_split if s.unstable]
    else:
        picked = []
    return tuple(s.case_id for s in sorted(picked, key=lambda s: s.case_id)[:MAX_CASES])


def build_trace(
    spec: Spec, results: Sequence[CheckResult], run_stats: Mapping[str, CaseStat]
) -> tuple[TraceRow, ...]:
    by_id = {result.check_id: result for result in results if result.kind == "requirement"}
    stats = list(run_stats.values())
    rows: list[TraceRow] = []
    for requirement in spec.requirements:
        result = by_id.get(requirement.id)
        details = result.details if result is not None else {}
        status = str(details.get("status", "no_evidence"))
        rows.append(
            TraceRow(
                requirement=requirement,
                status=status,
                enforced=bool(result.enforced) if result is not None else False,
                measured=details.get("value"),
                compared=details.get("compared"),
                n=int(details.get("n", 0)),
                failing_cases=_failing(requirement, stats) if status == "not_met" else (),
            )
        )
    return tuple(rows)
